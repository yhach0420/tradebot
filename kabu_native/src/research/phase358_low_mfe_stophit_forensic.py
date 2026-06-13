"""
Phase358: Low-MFE stop_hit forensic — ENTRY pattern discovery (post-Phase355 population).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase357_actual_exit_audit import (
    MAX_DAY,
    MIN_DAY,
    _load_session_trades,
    classify_exit_reason,
)
from small_paper.board_dynamic_trailing_shadow import board_tier_from_percentile
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")

LOW_MFE_THRESHOLD_PCT = 0.3

def _is_low_mfe_stop(row: Mapping[str, Any]) -> bool:
    return row.get("exit_reason_canonical") == "stop_hit" and (
        _float(row.get("peak_mfe_pct")) or 0.0
    ) < LOW_MFE_THRESHOLD_PCT


NEAR_LIMIT_PCT = 0.5
LOW_LIQ_TRADING_VALUE_MIN = 1e8
LOW_LIQ_TURNOVER_PROXY_MIN = 0.002

PATTERN_IDS = (
    "A_limit_up_proximity_fade",
    "B_pullback_misread",
    "C_gap_up_fade",
    "D_low_liquidity",
    "E_other",
)

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen_100",
    "pnl_pct",
    "peak_mfe_pct",
    "exit_reason_canonical",
    "forensic_pattern",
    "phase355_would_block",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "entry_quality",
    "entry_score_v2",
    "entry_expectancy_score_v2",
    "entry_imbalance_percentile",
    "entry_order_book_imbalance",
    "momentum_continuation_score",
    "board_dynamic_tier",
    "turnover_proxy",
    "trading_value",
    "distance_to_limit_up_pct",
    "day_high_near_limit",
    "low_liquidity_shadow_rejected",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _pattern_a(trade: Mapping[str, Any]) -> bool:
    dist = _float(trade.get("distance_to_limit_up_pct"))
    if dist is not None and dist <= NEAR_LIMIT_PCT:
        return True
    if _bool(trade.get("day_high_near_limit")):
        return True
    if _bool(trade.get("near_limit_up")) or _bool(trade.get("is_limit_up")):
        return True
    return False


def _pattern_b(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
    return rise5 is not None and rise5 < 0 and vwap_dev is not None and vwap_dev < 0


def _pattern_c(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    rise10 = _float(trade.get("entry_rise_10min_pct"))
    vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
    if rise5 is not None and rise5 >= 0.3 and vwap_dev is not None and vwap_dev > 0:
        return True
    if rise10 is not None and rise10 >= 0.5 and rise5 is not None and rise5 < 0:
        return True
    return False


def _pattern_d(trade: Mapping[str, Any]) -> bool:
    if _bool(trade.get("low_liquidity_shadow_rejected")):
        return True
    tv = _float(trade.get("trading_value"))
    tp = _float(trade.get("turnover_proxy"))
    if tv is not None and tv < LOW_LIQ_TRADING_VALUE_MIN:
        return True
    if tp is not None and tp < LOW_LIQ_TURNOVER_PROXY_MIN:
        return True
    return False


def classify_forensic_pattern(trade: Mapping[str, Any]) -> str:
    if _pattern_a(trade):
        return "A_limit_up_proximity_fade"
    if _pattern_b(trade):
        return "B_pullback_misread"
    if _pattern_c(trade):
        return "C_gap_up_fade"
    if _pattern_d(trade):
        return "D_low_liquidity"
    return "E_other"


def entry_signature_matches(trade: Mapping[str, Any], pattern: str) -> bool:
    return classify_forensic_pattern(trade) == pattern


def enrich_forensic_trade(
    trade: Mapping[str, Any],
    *,
    acc: Mapping[str, str],
    universe_row: Mapping[str, str],
) -> dict[str, Any]:
    from universe.am_pm_universe import estimate_daily_limit_prices, limit_status_from_prices

    acc = dict(acc)
    ex = dict(trade)
    sym = str(ex.get("symbol") or "")
    entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price"))
    prev_close = _float(universe_row.get("close_price"))
    near_high = _float(acc.get("entry_near_day_high_pct") or ex.get("entry_near_day_high_pct"))
    rise5 = _float(acc.get("entry_rise_5min_pct") or ex.get("entry_rise_5min_pct"))
    rise10 = _float(acc.get("entry_rise_10min_pct") or ex.get("entry_rise_10min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or ex.get("entry_vwap_dev_pct"))
    imb_pct = _float(ex.get("entry_imbalance_percentile") or acc.get("entry_imbalance_percentile"))
    tier = ex.get("board_dynamic_trailing_tier") or board_tier_from_percentile(imb_pct)

    lim_up, lim_down, _ = estimate_daily_limit_prices(prev_close)
    lim = limit_status_from_prices(
        current=entry_px,
        limit_up=lim_up,
        limit_down=lim_down,
        bid_qty=None,
        ask_qty=None,
    )
    dist_up = _float(lim.get("distance_to_limit_up_pct"))
    day_high_near = False
    if entry_px and near_high is not None and near_high < 100 and lim_up and lim_up > 0:
        implied_high = entry_px / (1.0 - near_high / 100.0)
        day_high_near = (lim_up - implied_high) / lim_up * 100.0 <= NEAR_LIMIT_PCT

    block_fields = {
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "universe_slot": ex.get("universe_slot") or universe_row.get("universe_slot"),
        "source_bucket": ex.get("source_bucket") or universe_row.get("source_bucket"),
    }

    row: dict[str, Any] = {
        **dict(ex),
        "peak_mfe_pct": _float(ex.get("peak_mfe_pct"))
        or _float(ex.get("rolling_mfe_pct"))
        or _float(acc.get("rolling_mfe_pct")),
        "entry_rise_5min_pct": rise5,
        "entry_rise_10min_pct": rise10,
        "entry_vwap_dev_pct": vwap_dev,
        "entry_quality": _float(acc.get("continuation_quality_score") or ex.get("continuation_quality_score")),
        "entry_score_v2": _float(acc.get("entry_expectancy_score_v2") or ex.get("entry_expectancy_score_v2")),
        "entry_expectancy_score_v2": _float(
            acc.get("entry_expectancy_score_v2") or ex.get("entry_expectancy_score_v2")
        ),
        "entry_imbalance_percentile": imb_pct,
        "entry_order_book_imbalance": _float(
            acc.get("entry_order_book_imbalance") or ex.get("entry_order_book_imbalance")
        ),
        "momentum_continuation_score": _float(
            acc.get("momentum_continuation_score") or ex.get("momentum_continuation_score")
        ),
        "board_dynamic_tier": tier,
        "turnover_proxy": _float(acc.get("turnover_proxy") or ex.get("turnover_proxy")),
        "trading_value": _float(acc.get("trading_value") or ex.get("trading_value")),
        "distance_to_limit_up_pct": dist_up,
        "day_high_near_limit": day_high_near,
        "near_limit_up": _bool(lim.get("near_limit_up")),
        "is_limit_up": _bool(lim.get("is_limit_up")),
        "low_liquidity_shadow_rejected": _bool(
            acc.get("low_liquidity_shadow_rejected") or ex.get("low_liquidity_shadow_rejected")
        ),
        "phase355_would_block": would_block_pullback_dynamic40_shadow(block_fields),
        "exit_reason_canonical": classify_exit_reason(ex),
    }
    row["forensic_pattern"] = classify_forensic_pattern(row)
    return row


def load_session_forensic_trades(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    from small_paper.limit_up_proximity_entry_guard_shadow import (
        _infer_session_kind,
        _load_session_summary,
        _load_universe,
        _universe_path_for_session,
    )
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = _load_session_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "forensic_kept": [], "forensic_all_kept": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    summary = _load_session_summary(sess_dir)
    session_kind = str(base.get("session_kind") or "")
    day = str(session_meta.get("day_key") or session_meta.get("day") or "")
    universe = _load_universe(_universe_path_for_session(day, session_kind, summary, reports_dir))

    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    forensic_kept: list[dict[str, Any]] = []
    forensic_all_kept: list[dict[str, Any]] = []
    for trade in base.get("kept") or []:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        sym = str(trade.get("symbol") or "")
        uni_row = universe.get(sym, {})
        enriched = enrich_forensic_trade(trade, acc=acc, universe_row=uni_row)
        forensic_all_kept.append(enriched)
        is_low_mfe_stop = _is_low_mfe_stop(enriched)
        if is_low_mfe_stop:
            forensic_kept.append(enriched)

    return {
        **base,
        "forensic_kept": forensic_kept,
        "forensic_all_kept": forensic_all_kept,
        "error": "",
    }


@dataclass
class _PatternAccum:
    count: int = 0
    total_pnl_yen_100: float = 0.0
    stop_hit_low_mfe_count: int = 0
    all_matching_population_count: int = 0
    all_matching_stop_hit_count: int = 0

    def ingest_forensic(self, row: Mapping[str, Any]) -> None:
        self.count += 1
        self.total_pnl_yen_100 += float(_float(row.get("pnl_yen_100")) or 0.0)
        self.stop_hit_low_mfe_count += 1

    def ingest_population_match(self, row: Mapping[str, Any], *, is_low_mfe_stop: bool) -> None:
        self.all_matching_population_count += 1
        if is_low_mfe_stop:
            self.all_matching_stop_hit_count += 1


@dataclass
class Phase358ForensicAudit:
    reports_dir: Path
    low_mfe_trades: list[dict[str, Any]] = field(default_factory=list)
    all_kept_trades: list[dict[str, Any]] = field(default_factory=list)
    sessions_loaded: int = 0

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase358_low_mfe_stophit_forensic_summary.json",
            "by_pattern": self.reports_dir / "phase358_low_mfe_stophit_by_pattern.csv",
            "by_symbol": self.reports_dir / "phase358_low_mfe_stophit_by_symbol.csv",
            "trades": self.reports_dir / "phase358_low_mfe_stophit_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.sessions_loaded += 1
        self.low_mfe_trades.extend(result.get("forensic_kept") or [])
        self.all_kept_trades.extend(result.get("forensic_all_kept") or [])

    def _counterfactual_exclude(self, pattern: str) -> dict[str, Any]:
        removed = [t for t in self.all_kept_trades if entry_signature_matches(t, pattern)]
        kept = [t for t in self.all_kept_trades if not entry_signature_matches(t, pattern)]
        actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.all_kept_trades]
        new_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
        actual_total = round(sum(actual_yens), 2)
        new_total = round(sum(new_yens), 2)
        return {
            "removed_trades": len(removed),
            "removed_pnl_yen_100": round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed), 2),
            "delta_pnl_yen_100": round(new_total - actual_total, 2),
            "actual_pf": _pf(actual_yens),
            "counterfactual_pf": _pf(new_yens),
            "delta_pf": (
                round((_pf(new_yens) or 0) - (_pf(actual_yens) or 0), 4)
                if _pf(new_yens) is not None and _pf(actual_yens) is not None
                and _pf(new_yens) != float("inf")
                and _pf(actual_yens) != float("inf")
                else None
            ),
        }

    def _rollup_patterns(
        self,
        trades: Sequence[Mapping[str, Any]],
        *,
        universe_filter: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        subset = [
            t
            for t in trades
            if universe_filter is None or str(t.get("universe_group") or "") == universe_filter
        ]
        total_loss = sum(
            float(_float(t.get("pnl_yen_100")) or 0.0)
            for t in subset
            if float(_float(t.get("pnl_yen_100")) or 0.0) < 0
        )
        acc: dict[str, _PatternAccum] = defaultdict(_PatternAccum)

        for t in self.all_kept_trades:
            if universe_filter and str(t.get("universe_group") or "") != universe_filter:
                continue
            pat = classify_forensic_pattern(t)
            is_low = _is_low_mfe_stop(t)
            acc[pat].ingest_population_match(t, is_low_mfe_stop=is_low)

        for t in subset:
            pat = str(t.get("forensic_pattern") or classify_forensic_pattern(t))
            acc[pat].ingest_forensic(t)

        rows: dict[str, dict[str, Any]] = {}
        for pat in PATTERN_IDS:
            a = acc.get(pat, _PatternAccum())
            stop_rate = (
                round(a.all_matching_stop_hit_count / a.all_matching_population_count, 4)
                if a.all_matching_population_count
                else None
            )
            share_loss = (
                round(a.total_pnl_yen_100 / total_loss, 4)
                if total_loss < 0 and a.total_pnl_yen_100 < 0
                else None
            )
            rows[pat] = {
                "pattern": pat,
                "count": a.count,
                "total_pnl_yen_100": round(a.total_pnl_yen_100, 2),
                "avg_pnl_yen_100": round(a.total_pnl_yen_100 / a.count, 2) if a.count else 0.0,
                "stop_hit_low_mfe_rate_in_pattern": stop_rate,
                "share_of_low_mfe_stop_loss": share_loss,
                "population_matches": a.all_matching_population_count,
                "counterfactual_if_excluded": self._counterfactual_exclude(pat),
            }
        return rows

    def _by_symbol(self) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_pnl_yen_100": 0.0, "patterns": Counter()}
        )

        for t in self.low_mfe_trades:
            sym = str(t.get("symbol") or "")
            acc[sym]["count"] += 1
            acc[sym]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            acc[sym]["patterns"][str(t.get("forensic_pattern") or "E_other")] += 1
        rows = []
        for sym, v in sorted(acc.items(), key=lambda x: x[1]["total_pnl_yen_100"]):
            top_pat = v["patterns"].most_common(1)[0][0] if v["patterns"] else ""
            rows.append(
                {
                    "symbol": sym,
                    "count": v["count"],
                    "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                    "avg_pnl_yen_100": round(v["total_pnl_yen_100"] / v["count"], 2),
                    "dominant_pattern": top_pat,
                    "pattern_counts": dict(v["patterns"]),
                }
            )
        return rows

    def build_summary(self) -> dict[str, Any]:
        total_loss = round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.low_mfe_trades), 2
        )
        all_patterns = self._rollup_patterns(self.low_mfe_trades)
        dyn_patterns = self._rollup_patterns(self.low_mfe_trades, universe_filter="dynamic40")
        core_patterns = self._rollup_patterns(self.low_mfe_trades, universe_filter="core10")

        b_uncovered = [
            t
            for t in self.low_mfe_trades
            if t.get("forensic_pattern") == "B_pullback_misread" and not t.get("phase355_would_block")
        ]
        b_dyn_not_blocked = [
            t
            for t in self.low_mfe_trades
            if t.get("forensic_pattern") == "B_pullback_misread"
            and str(t.get("universe_group") or "") == "dynamic40"
            and not t.get("phase355_would_block")
        ]

        worst = min(all_patterns.items(), key=lambda x: x[1]["total_pnl_yen_100"], default=(None, {}))
        best_guard = max(
            PATTERN_IDS,
            key=lambda p: (all_patterns.get(p, {}).get("counterfactual_if_excluded") or {}).get(
                "delta_pnl_yen_100", 0.0
            ),
        )

        return {
            "phase": 358,
            "title": "low_mfe_stophit_forensic",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_post_excluded",
            "date_range": {"min_day": MIN_DAY, "max_day": MAX_DAY},
            "sessions_loaded": self.sessions_loaded,
            "low_mfe_stop_hit_count": len(self.low_mfe_trades),
            "low_mfe_stop_hit_total_pnl_yen_100": total_loss,
            "low_mfe_threshold_pct": LOW_MFE_THRESHOLD_PCT,
            "by_pattern_all": all_patterns,
            "by_pattern_dynamic40": dyn_patterns,
            "by_pattern_core10": core_patterns,
            "answers": {
                "q1_max_loss_pattern": worst[0],
                "q1_max_loss_pnl_yen_100": worst[1].get("total_pnl_yen_100") if worst[0] else None,
                "q2_phase355_uncovered_b_loss": {
                    "count": len(b_uncovered),
                    "total_pnl_yen_100": round(
                        sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in b_uncovered), 2
                    ),
                    "note": "B_pullback_misread low-MFE stops not blocked by Phase355 Dynamic40 guard",
                },
                "q2_dynamic40_b_slippage": {
                    "count": len(b_dyn_not_blocked),
                    "total_pnl_yen_100": round(
                        sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in b_dyn_not_blocked), 2
                    ),
                },
                "q3_next_entry_guard_candidate": best_guard,
                "q4_best_counterfactual": all_patterns.get(best_guard, {}).get(
                    "counterfactual_if_excluded"
                ),
            },
            "pattern_definitions": {
                "A_limit_up_proximity_fade": f"distance_to_limit_up<={NEAR_LIMIT_PCT}% or day_high_near_limit",
                "B_pullback_misread": "entry_rise_5min<0 AND entry_vwap_dev<0",
                "C_gap_up_fade": "rise5>=0.3%&vwap_dev>0 OR rise10>=0.5%&rise5<0",
                "D_low_liquidity": f"low_liq_shadow OR trading_value<{LOW_LIQ_TRADING_VALUE_MIN} OR turnover<{LOW_LIQ_TURNOVER_PROXY_MIN}",
                "E_other": "none of above",
            },
        }

    def by_pattern_rows(self) -> list[dict[str, Any]]:
        summary = self.build_summary()
        rows: list[dict[str, Any]] = []
        for scope, key in (
            ("all", "by_pattern_all"),
            ("dynamic40", "by_pattern_dynamic40"),
            ("core10", "by_pattern_core10"),
        ):
            for pat, met in (summary.get(key) or {}).items():
                cf = met.get("counterfactual_if_excluded") or {}
                rows.append(
                    {
                        "universe_scope": scope,
                        "pattern": pat,
                        "count": met.get("count"),
                        "total_pnl_yen_100": met.get("total_pnl_yen_100"),
                        "avg_pnl_yen_100": met.get("avg_pnl_yen_100"),
                        "stop_hit_low_mfe_rate_in_pattern": met.get("stop_hit_low_mfe_rate_in_pattern"),
                        "share_of_low_mfe_stop_loss": met.get("share_of_low_mfe_stop_loss"),
                        "population_matches": met.get("population_matches"),
                        "cf_removed_trades": cf.get("removed_trades"),
                        "cf_delta_pnl_yen_100": cf.get("delta_pnl_yen_100"),
                        "cf_delta_pf": cf.get("delta_pf"),
                    }
                )
        return rows

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._write_csv(paths["by_pattern"], self.by_pattern_rows())
        sym_rows = self._by_symbol()
        if sym_rows:
            fields = sorted({k for r in sym_rows for k in r})
            with paths["by_symbol"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in sym_rows:
                    flat = dict(row)
                    pc = flat.pop("pattern_counts", None)
                    if isinstance(pc, dict):
                        for k, v in pc.items():
                            flat[f"pattern_{k}"] = v
                    w.writerow(flat)
        self._write_csv(paths["trades"], self.low_mfe_trades, TRADE_FIELDS)
        return {k: str(v) for k, v in paths.items()}

    def _write_csv(
        self,
        path: Path,
        rows: Sequence[Mapping[str, Any]],
        fieldnames: Optional[Sequence[str]] = None,
    ) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fields = list(fieldnames) if fieldnames else sorted({k for r in rows for k in r})
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)

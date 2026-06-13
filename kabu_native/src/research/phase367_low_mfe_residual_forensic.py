"""
Phase367: Post-Phase364 low-MFE stop_hit residual forensic.

Population: Phase355+364 production kept trades with stop_hit and peak_mfe < 0.3%.
Analysis only — no production adoption.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase360_eother_classification import (
    TIME_BUCKETS,
    _price_range_position,
    entry_time_bucket,
)
from research.phase365_production_stack_validation import (
    load_session_production_stack_trades,
    stack_blocked,
)
from research.phase366_stophit_reclassification import MIN_DAY, production_kept_trades
from small_paper.board_dynamic_trailing_shadow import board_tier_from_percentile
from small_paper.near_day_high_low_mom_entry_guard_shadow import would_block_near_day_high_low_mom_guard
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")
LOW_MFE_THRESHOLD_PCT = 0.3
LOW_LIQ_TRADING_VALUE_MIN = 1e8
LOW_LIQ_TURNOVER_PROXY_MIN = 0.002
LATE_TIME_BUCKETS = frozenset({"12:30-14:00", "14:00-15:00", "other"})

PATTERN_IDS = (
    "A1_board_low_low_momentum",
    "A2_vwap_below_not_pullback",
    "A3_high_range_top",
    "A4_low_liquidity",
    "A5_late_session_weak_entry",
    "A6_symbol_reentry_cluster",
    "A7_other",
)

PATTERN_LABELS = {
    "A1_board_low_low_momentum": "board_low + entry_momentum < 0.30",
    "A2_vwap_below_not_pullback": "entry_vwap_dev < 0 outside Phase355 pullback",
    "A3_high_range_top": "price_range_position >= 0.85",
    "A4_low_liquidity": "low trading_value or turnover_proxy",
    "A5_late_session_weak_entry": "PM/late bucket + weak momentum",
    "A6_symbol_reentry_cluster": "repeat low-MFE stop same symbol/day",
    "A7_other": "unclassified residual",
}

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "entry_time_bucket",
    "pnl_yen_100",
    "pnl_pct",
    "peak_mfe_pct",
    "exit_reason_canonical",
    "residual_pattern",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "entry_momentum_score",
    "day_high_distance_pct",
    "price_range_position",
    "entry_imbalance_percentile",
    "board_dynamic_tier",
    "trading_value",
    "turnover_proxy",
    "phase355_would_block",
    "phase364_would_block",
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


def _is_low_mfe_stop(row: Mapping[str, Any]) -> bool:
    return row.get("exit_reason_canonical") == "stop_hit" and (
        _float(row.get("peak_mfe_pct")) or 0.0
    ) < LOW_MFE_THRESHOLD_PCT


def _is_board_low(trade: Mapping[str, Any]) -> bool:
    tier = str(trade.get("board_dynamic_tier") or "")
    if tier == "board_low":
        return True
    pctile = _float(trade.get("entry_imbalance_percentile"))
    return pctile is not None and pctile < 25.0


def _is_weak_momentum(trade: Mapping[str, Any], threshold: float = 0.30) -> bool:
    mom = _float(trade.get("entry_momentum_score"))
    return mom is not None and mom < threshold


def _match_a1(trade: Mapping[str, Any]) -> bool:
    return _is_board_low(trade) and _is_weak_momentum(trade)


def _match_a2(trade: Mapping[str, Any]) -> bool:
    vwap = _float(trade.get("entry_vwap_dev_pct"))
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    if vwap is None or vwap >= 0:
        return False
    if rise5 is not None and rise5 < 0:
        return False
    return True


def _match_a3(trade: Mapping[str, Any]) -> bool:
    pos = _float(trade.get("price_range_position"))
    return pos is not None and pos >= 0.85


def _match_a4(trade: Mapping[str, Any]) -> bool:
    if _bool(trade.get("low_liquidity_shadow_rejected")):
        return True
    tv = _float(trade.get("trading_value"))
    tp = _float(trade.get("turnover_proxy"))
    if tv is not None and tv < LOW_LIQ_TRADING_VALUE_MIN:
        return True
    if tp is not None and tp < LOW_LIQ_TURNOVER_PROXY_MIN:
        return True
    return False


def _match_a5(trade: Mapping[str, Any]) -> bool:
    bucket = str(trade.get("entry_time_bucket") or "")
    sk = str(trade.get("session_kind") or "")
    if sk == "pm" and bucket in LATE_TIME_BUCKETS and _is_weak_momentum(trade):
        return True
    if bucket == "14:00-15:00" and _is_weak_momentum(trade):
        return True
    return False


def _match_a6(trade: Mapping[str, Any]) -> bool:
    return bool(trade.get("symbol_reentry_cluster"))


PATTERN_SPECS: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
    ("A4_low_liquidity", _match_a4),
    ("A1_board_low_low_momentum", _match_a1),
    ("A3_high_range_top", _match_a3),
    ("A2_vwap_below_not_pullback", _match_a2),
    ("A5_late_session_weak_entry", _match_a5),
    ("A6_symbol_reentry_cluster", _match_a6),
]


def pattern_guard_match(trade: Mapping[str, Any], pattern_id: str) -> bool:
    if pattern_id == "A7_other":
        return assign_residual_pattern(trade) == "A7_other"
    for pid, fn in PATTERN_SPECS:
        if pid == pattern_id:
            return fn(trade)
    return False


def assign_residual_pattern(trade: Mapping[str, Any]) -> str:
    for pid, fn in PATTERN_SPECS:
        if fn(trade):
            return pid
    return "A7_other"


def _mark_symbol_reentry_clusters(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_day_sym: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        key = (str(row.get("day_key") or ""), str(row.get("symbol") or ""))
        by_day_sym[key].append(row)

    for key, rows in by_day_sym.items():
        rows.sort(key=lambda r: str(r.get("entry_time") or ""))
        for i, row in enumerate(rows):
            row["symbol_reentry_cluster"] = i > 0
            row["symbol_day_stop_index"] = i + 1
            out.append(row)
    out.sort(key=lambda r: (str(r.get("day_key") or ""), str(r.get("entry_time") or "")))
    return out


def enrich_residual_trade(trade: Mapping[str, Any], acc: Mapping[str, str]) -> dict[str, Any]:
    near_high = _float(
        acc.get("entry_near_day_high_pct")
        or trade.get("entry_near_day_high_pct")
        or trade.get("day_high_distance_pct")
    )
    intraday_range = _float(acc.get("intraday_range_pct") or trade.get("intraday_range_pct"))
    mom = _float(
        acc.get("entry_momentum_continuation_score")
        or acc.get("momentum_continuation_score")
        or trade.get("entry_momentum_score")
        or trade.get("momentum_continuation_score")
    )
    imb_pctile = _float(
        acc.get("entry_imbalance_percentile") or trade.get("entry_imbalance_percentile")
    )
    rise5 = _float(acc.get("entry_rise_5min_pct") or trade.get("entry_rise_5min_pct"))
    rise10 = _float(acc.get("entry_rise_10min_pct") or trade.get("entry_rise_10min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or trade.get("entry_vwap_dev_pct"))
    entry_time = str(trade.get("entry_time") or "")
    tier = trade.get("board_dynamic_trailing_tier") or board_tier_from_percentile(imb_pctile)

    block_fields = {
        "entry_rise_5min_pct": rise5,
        "entry_vwap_dev_pct": vwap_dev,
        "universe_slot": trade.get("universe_slot"),
        "source_bucket": trade.get("source_bucket"),
        "universe_bucket": trade.get("universe_bucket"),
        "day_high_distance_pct": near_high,
        "entry_near_day_high_pct": near_high,
        "entry_momentum_score": mom,
    }

    return {
        **dict(trade),
        "entry_rise_5min_pct": rise5,
        "entry_rise_10min_pct": rise10,
        "entry_vwap_dev_pct": vwap_dev,
        "entry_momentum_score": mom,
        "day_high_distance_pct": near_high,
        "price_range_position": _price_range_position(near_high, intraday_range),
        "entry_imbalance_percentile": imb_pctile,
        "board_dynamic_tier": tier,
        "trading_value": _float(acc.get("trading_value") or trade.get("trading_value")),
        "turnover_proxy": _float(acc.get("turnover_proxy") or trade.get("turnover_proxy")),
        "low_liquidity_shadow_rejected": _bool(
            acc.get("low_liquidity_shadow_rejected") or trade.get("low_liquidity_shadow_rejected")
        ),
        "entry_time_bucket": entry_time_bucket(entry_time),
        "phase355_would_block": would_block_pullback_dynamic40_shadow(block_fields),
        "phase364_would_block": would_block_near_day_high_low_mom_guard(block_fields),
        "peak_mfe_pct": _float(trade.get("peak_mfe_pct")),
    }


def load_session_residual_forensic(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "residual_trades": [], "all_production_enriched": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    all_enriched: list[dict[str, Any]] = []
    for trade in production_kept_trades(base):
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_residual_trade(trade, acc)
        row["residual_pattern"] = ""
        all_enriched.append(row)

    low_mfe = [t for t in all_enriched if _is_low_mfe_stop(t)]
    low_mfe = _mark_symbol_reentry_clusters(low_mfe)
    for row in low_mfe:
        row["residual_pattern"] = assign_residual_pattern(row)

    pattern_by_key = {
        (t.get("symbol", ""), t.get("entry_time", "")): t["residual_pattern"] for t in low_mfe
    }
    for row in all_enriched:
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        if key in pattern_by_key:
            row["residual_pattern"] = pattern_by_key[key]
            src = next(
                t for t in low_mfe if t.get("symbol") == key[0] and t.get("entry_time") == key[1]
            )
            row["symbol_reentry_cluster"] = src.get("symbol_reentry_cluster", False)

    return {
        **base,
        "residual_trades": low_mfe,
        "all_production_enriched": all_enriched,
        "residual_count": len(low_mfe),
        "error": "",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _counterfactual(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    pattern_id: str,
) -> dict[str, Any]:
    removed = [t for t in all_trades if pattern_guard_match(t, pattern_id)]
    kept = [t for t in all_trades if not pattern_guard_match(t, pattern_id)]
    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_trades]
    new_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
    actual_total = round(sum(actual_yens), 2)
    new_total = round(sum(new_yens), 2)
    removed_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed), 2)
    low_mfe_removed = [t for t in removed if _is_low_mfe_stop(t)]
    stop_removed = [t for t in removed if t.get("exit_reason_canonical") == "stop_hit"]
    return {
        "pattern_id": pattern_id,
        "removed_trades": len(removed),
        "skipped_pnl_actual": removed_pnl,
        "low_mfe_residual_removed_count": len(low_mfe_removed),
        "low_mfe_residual_removed_pnl": round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in low_mfe_removed), 2
        ),
        "delta_yen": round(new_total - actual_total, 2),
        "actual_pf": _pf(actual_yens),
        "counterfactual_pf": _pf(new_yens),
        "delta_pf": (
            round((_pf(new_yens) or 0) - (_pf(actual_yens) or 0), 4)
            if _pf(new_yens) is not None
            and _pf(actual_yens) is not None
            and _pf(new_yens) != float("inf")
            and _pf(actual_yens) != float("inf")
            else None
        ),
        "stop_hit_reduction_count": len(stop_removed),
        "low_mfe_stop_hit_reduction_count": len(low_mfe_removed),
    }


@dataclass
class Phase367LowMfeResidualForensic:
    reports_dir: Path
    residual_trades: list[dict[str, Any]] = field(default_factory=list)
    all_production_trades: list[dict[str, Any]] = field(default_factory=list)
    sessions_loaded: int = 0

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase367_low_mfe_residual_forensic_summary.json",
            "by_pattern": self.reports_dir / "phase367_low_mfe_residual_by_pattern.csv",
            "by_symbol": self.reports_dir / "phase367_low_mfe_residual_by_symbol.csv",
            "trades": self.reports_dir / "phase367_low_mfe_residual_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.sessions_loaded += 1
        self.residual_trades.extend(result.get("residual_trades") or [])
        self.all_production_trades.extend(result.get("all_production_enriched") or [])

    def _pattern_rollup(
        self,
        trades: Sequence[Mapping[str, Any]],
        *,
        universe_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
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
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "total_pnl_yen_100": 0.0,
                "symbols": Counter(),
                "time_buckets": Counter(),
                "universe": Counter(),
            }
        )
        for t in subset:
            pat = str(t.get("residual_pattern") or assign_residual_pattern(t))
            acc[pat]["count"] += 1
            acc[pat]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            acc[pat]["symbols"][str(t.get("symbol") or "")] += 1
            acc[pat]["time_buckets"][str(t.get("entry_time_bucket") or "")] += 1
            acc[pat]["universe"][str(t.get("universe_group") or "")] += 1

        rows: list[dict[str, Any]] = []
        for pid in PATTERN_IDS:
            a = acc.get(pid, {"count": 0, "total_pnl_yen_100": 0.0, "symbols": Counter(), "time_buckets": Counter(), "universe": Counter()})
            count = int(a["count"])
            total = round(float(a["total_pnl_yen_100"]), 2)
            cf = _counterfactual(self.all_production_trades, pattern_id=pid)
            rows.append(
                {
                    "pattern_id": pid,
                    "label": PATTERN_LABELS.get(pid, pid),
                    "count": count,
                    "total_pnl_yen_100": total,
                    "avg_pnl_yen_100": round(total / count, 2) if count else None,
                    "profit_factor": _pf(
                        [
                            float(_float(t.get("pnl_yen_100")) or 0.0)
                            for t in subset
                            if str(t.get("residual_pattern") or "") == pid
                        ]
                    ),
                    "share_of_residual_loss": round(total / total_loss, 4)
                    if total_loss < 0 and total < 0
                    else None,
                    "share_of_residual_count": round(count / len(subset), 4) if subset else 0.0,
                    "dominant_symbol": a["symbols"].most_common(1)[0][0] if a["symbols"] else "",
                    "dominant_time_bucket": a["time_buckets"].most_common(1)[0][0]
                    if a["time_buckets"]
                    else "",
                    "dynamic40_count": int(a["universe"].get("dynamic40", 0)),
                    "core10_count": int(a["universe"].get("core10", 0)),
                    **cf,
                }
            )
        return rows

    def _by_symbol(self) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_pnl_yen_100": 0.0, "patterns": Counter(), "universe": Counter()}
        )
        for t in self.residual_trades:
            sym = str(t.get("symbol") or "")
            acc[sym]["count"] += 1
            acc[sym]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            acc[sym]["patterns"][str(t.get("residual_pattern") or "A7_other")] += 1
            acc[sym]["universe"][str(t.get("universe_group") or "")] += 1
        rows = []
        for sym, v in sorted(acc.items(), key=lambda x: x[1]["total_pnl_yen_100"]):
            rows.append(
                {
                    "symbol": sym,
                    "count": v["count"],
                    "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                    "avg_pnl_yen_100": round(v["total_pnl_yen_100"] / v["count"], 2),
                    "dominant_pattern": v["patterns"].most_common(1)[0][0] if v["patterns"] else "",
                    "dynamic40_count": int(v["universe"].get("dynamic40", 0)),
                    "core10_count": int(v["universe"].get("core10", 0)),
                }
            )
        return rows

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        paths = self.paths()
        total_loss = round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.residual_trades), 2
        )
        all_patterns = self._pattern_rollup(self.residual_trades)
        dyn_patterns = self._pattern_rollup(self.residual_trades, universe_filter="dynamic40")
        core_patterns = self._pattern_rollup(self.residual_trades, universe_filter="core10")

        worst = min(all_patterns, key=lambda r: r["total_pnl_yen_100"], default={})
        best_cf = max(all_patterns, key=lambda r: r.get("delta_yen") or 0.0, default={})

        phase355_overlap = sum(1 for t in self.residual_trades if t.get("phase355_would_block"))
        phase364_overlap = sum(1 for t in self.residual_trades if t.get("phase364_would_block"))

        by_universe = {}
        for ug in ("dynamic40", "core10"):
            sub = [t for t in self.residual_trades if str(t.get("universe_group") or "") == ug]
            yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in sub]
            by_universe[ug] = {
                "count": len(sub),
                "total_pnl_yen_100": round(sum(yens), 2) if yens else 0.0,
                "avg_pnl_yen_100": round(sum(yens) / len(yens), 2) if yens else None,
                "profit_factor": _pf(yens),
            }

        summary = {
            "phase": 367,
            "title": "post_phase364_low_mfe_residual_forensic",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_kept_low_mfe_stop_hit",
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_loaded": self.sessions_loaded,
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "residual_stop_count": len(self.residual_trades),
            "total_residual_loss_yen_100": total_loss,
            "production_trade_count": len(self.all_production_trades),
            "phase355_overlap_count": phase355_overlap,
            "phase364_overlap_count": phase364_overlap,
            "by_universe": by_universe,
            "by_pattern": {r["pattern_id"]: r for r in all_patterns},
            "by_pattern_dynamic40": {r["pattern_id"]: r for r in dyn_patterns},
            "by_pattern_core10": {r["pattern_id"]: r for r in core_patterns},
            "conclusion": {
                "largest_residual_loss_pattern": worst.get("pattern_id"),
                "largest_residual_loss_yen_100": worst.get("total_pnl_yen_100"),
                "best_counterfactual_pattern": best_cf.get("pattern_id"),
                "best_counterfactual_delta_yen": best_cf.get("delta_yen"),
                "best_counterfactual_delta_pf": best_cf.get("delta_pf"),
                "best_counterfactual_stop_hit_reduction": best_cf.get("stop_hit_reduction_count"),
                "adoptable_guard_candidate": best_cf.get("pattern_id")
                if (best_cf.get("delta_yen") or 0) > 0
                and (best_cf.get("low_mfe_residual_removed_pnl") or 0) < 0
                else None,
                "expected_improvement_yen_100": best_cf.get("delta_yen"),
                "analysis_only": True,
                "recommendation": (
                    f"Shadow-validate {best_cf.get('pattern_id')} before any production guard; "
                    f"counterfactual delta={best_cf.get('delta_yen')} yen (analysis only)."
                    if (best_cf.get("delta_yen") or 0) > 0
                    else "No single residual pattern shows clear counterfactual uplift; "
                    "consider composite shadow review."
                ),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if all_patterns:
            _write_csv(
                paths["by_pattern"],
                all_patterns,
                sorted({k for r in all_patterns for k in r}),
            )
        symbol_rows = self._by_symbol()
        if symbol_rows:
            _write_csv(
                paths["by_symbol"],
                symbol_rows,
                [
                    "symbol",
                    "count",
                    "total_pnl_yen_100",
                    "avg_pnl_yen_100",
                    "dominant_pattern",
                    "dynamic40_count",
                    "core10_count",
                ],
            )
        if self.residual_trades:
            _write_csv(paths["trades"], self.residual_trades, TRADE_FIELDS)
        return paths

"""
Phase372: Post-Phase364 low-MFE stop_hit immediate-death forensic + ENTRY cluster counterfactual.
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

from research.phase366_stophit_reclassification import MIN_DAY, production_kept_trades
from research.phase367_low_mfe_residual_forensic import (
    LATE_TIME_BUCKETS,
    LOW_LIQ_TRADING_VALUE_MIN,
    LOW_LIQ_TURNOVER_PROXY_MIN,
    _bool,
    _float,
    _is_board_low,
    _is_low_mfe_stop,
    _is_weak_momentum,
    _mark_symbol_reentry_clusters,
    _match_a1,
    _match_a2,
    _match_a3,
    _match_a4,
    _match_a5,
    _pf,
    enrich_residual_trade,
)
from research.phase365_production_stack_validation import load_session_production_stack_trades
from research.phase360_eother_classification import entry_time_bucket
from small_paper.high_mfe_stophit_exit_recovery_shadow import _build_tick_paths

JST = ZoneInfo("Asia/Tokyo")
LOW_MFE_THRESHOLD_PCT = 0.3
RESIDUAL_CLUSTER_IDS = frozenset({"C13_core10_residual", "C14_dyn40_residual"})
FORENSIC_ONLY_CLUSTER_IDS = frozenset(
    {
        "C01_dyn40_death60_board_low",
        "C02_dyn40_death120_neg_rise",
        "C03_dyn40_stop_approach_180s",
        "C12_core10_death60",
    }
)
PHASE368_FAILED_GUARDS = frozenset({"C09_dyn40_symbol_repeat"})
MAX_REMOVED_TRADES_ADOPT = 130
LOSS_60S_PCT = -0.3
LOSS_120S_PCT = -0.5
STOP_APPROACH_180S_PCT = -1.0

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
    "death_cluster",
    "residual_pattern",
    "loss_60s_0p3",
    "loss_120s_0p5",
    "stop_approach_180s",
    "min_pnl_first_60s",
    "min_pnl_first_120s",
    "min_pnl_first_180s",
    "sec_to_worst_pnl",
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
    "symbol_reentry_cluster",
    "phase355_would_block",
    "phase364_would_block",
]


def _is_dynamic40(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_group") or "") == "dynamic40"


def _is_core10(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_group") or "") == "core10"


def _is_negative_rise(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    return rise5 is not None and rise5 < 0


def _is_near_day_high(trade: Mapping[str, Any], threshold: float = 1.5) -> bool:
    dist = _float(trade.get("day_high_distance_pct"))
    return dist is not None and dist <= threshold


def _is_early_open(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("entry_time_bucket") or "") == "09:00-09:30"


def _is_pm_late(trade: Mapping[str, Any]) -> bool:
    bucket = str(trade.get("entry_time_bucket") or "")
    sk = str(trade.get("session_kind") or "")
    return sk == "pm" and bucket in LATE_TIME_BUCKETS


def annotate_immediate_death(
    ticks: Sequence[Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "loss_60s_0p3": False,
        "loss_120s_0p5": False,
        "stop_approach_180s": False,
        "min_pnl_first_60s": None,
        "min_pnl_first_120s": None,
        "min_pnl_first_180s": None,
        "sec_to_worst_pnl": None,
    }
    if not ticks:
        return out

    entry_ts = float(ticks[0].ts_epoch)
    worst_pnl = 0.0
    worst_sec: Optional[float] = None
    min60 = min120 = min180 = 0.0
    has60 = has120 = has180 = False

    for tick in ticks:
        elapsed = float(tick.ts_epoch) - entry_ts
        pnl = float(tick.pnl_pct)
        if pnl < worst_pnl:
            worst_pnl = pnl
            worst_sec = round(elapsed, 1)

        if elapsed <= 60:
            min60 = min(min60, pnl)
            has60 = True
            if pnl <= LOSS_60S_PCT:
                out["loss_60s_0p3"] = True
        if elapsed <= 120:
            min120 = min(min120, pnl)
            has120 = True
            if pnl <= LOSS_120S_PCT:
                out["loss_120s_0p5"] = True
        if elapsed <= 180:
            min180 = min(min180, pnl)
            has180 = True
            if pnl <= STOP_APPROACH_180S_PCT:
                out["stop_approach_180s"] = True

    if has60:
        out["min_pnl_first_60s"] = round(min60, 4)
    if has120:
        out["min_pnl_first_120s"] = round(min120, 4)
    if has180:
        out["min_pnl_first_180s"] = round(min180, 4)
    out["sec_to_worst_pnl"] = worst_sec
    return out


CLUSTER_SPECS: list[dict[str, Any]] = [
    {
        "cluster_id": "C01_dyn40_death60_board_low",
        "label": "Dynamic40 + 60s<=-0.3% + board_low",
        "priority": 1,
        "guard_rule": "dynamic40 AND loss_60s_0p3 AND board_low",
        "entry_guard_proxy": "board_low + weak_momentum early adverse",
        "match": lambda t: _is_dynamic40(t)
        and _bool(t.get("loss_60s_0p3"))
        and _is_board_low(t),
    },
    {
        "cluster_id": "C02_dyn40_death120_neg_rise",
        "label": "Dynamic40 + 120s<=-0.5% + rise5<0",
        "priority": 2,
        "guard_rule": "dynamic40 AND loss_120s_0p5 AND entry_rise_5min_pct<0",
        "entry_guard_proxy": "Dynamic40 pullback-negative entry",
        "match": lambda t: _is_dynamic40(t)
        and _bool(t.get("loss_120s_0p5"))
        and _is_negative_rise(t)
        and not _bool(t.get("phase355_would_block")),
    },
    {
        "cluster_id": "C03_dyn40_stop_approach_180s",
        "label": "Dynamic40 + 180s pnl<=-1.0%",
        "priority": 3,
        "guard_rule": "dynamic40 AND stop_approach_180s",
        "entry_guard_proxy": "immediate stop approach within 3min",
        "match": lambda t: _is_dynamic40(t) and _bool(t.get("stop_approach_180s")),
    },
    {
        "cluster_id": "C04_dyn40_a1_board_low_mom",
        "label": "Dynamic40 + A1 board_low weak momentum",
        "priority": 4,
        "guard_rule": "dynamic40 AND board_low AND mom<0.30",
        "entry_guard_proxy": "A1 board_low low momentum",
        "match": lambda t: _is_dynamic40(t) and _match_a1(t),
    },
    {
        "cluster_id": "C05_dyn40_near_day_high_weak",
        "label": "Dynamic40 + day_high<=1.5% + mom<0.30",
        "priority": 5,
        "guard_rule": "dynamic40 AND day_high_distance_pct<=1.5 AND mom<0.30",
        "entry_guard_proxy": "Phase364 overlap axis",
        "match": lambda t: _is_dynamic40(t)
        and _is_near_day_high(t)
        and _is_weak_momentum(t),
    },
    {
        "cluster_id": "C06_dyn40_early_open_a1",
        "label": "Dynamic40 + 09:00-09:30 + A1",
        "priority": 6,
        "guard_rule": "dynamic40 AND 09:00-09:30 AND A1",
        "entry_guard_proxy": "opening board_low weak momentum",
        "match": lambda t: _is_dynamic40(t) and _is_early_open(t) and _match_a1(t),
    },
    {
        "cluster_id": "C07_dyn40_pm_late_weak",
        "label": "Dynamic40 + PM late + weak momentum",
        "priority": 7,
        "guard_rule": "dynamic40 AND PM late bucket AND mom<0.30",
        "entry_guard_proxy": "late session weak entry",
        "match": lambda t: _is_dynamic40(t) and _is_pm_late(t) and _is_weak_momentum(t),
    },
    {
        "cluster_id": "C08_dyn40_low_liquidity",
        "label": "Dynamic40 + low liquidity",
        "priority": 8,
        "guard_rule": "dynamic40 AND (tv<1e8 OR turnover<0.002)",
        "entry_guard_proxy": "low trading_value / turnover",
        "match": lambda t: _is_dynamic40(t) and _match_a4(t),
    },
    {
        "cluster_id": "C09_dyn40_symbol_repeat",
        "label": "Dynamic40 + same-day symbol repeat low-MFE stop",
        "priority": 9,
        "guard_rule": "dynamic40 AND symbol_reentry_cluster",
        "entry_guard_proxy": "symbol reentry after prior low-MFE stop",
        "match": lambda t: _is_dynamic40(t) and _bool(t.get("symbol_reentry_cluster")),
    },
    {
        "cluster_id": "C10_high_range_top",
        "label": "price_range_position >= 0.85",
        "priority": 10,
        "guard_rule": "price_range_position>=0.85",
        "entry_guard_proxy": "near intraday range top",
        "match": lambda t: _match_a3(t),
    },
    {
        "cluster_id": "C11_vwap_below_not_pullback",
        "label": "vwap<0 outside Phase355 pullback",
        "priority": 11,
        "guard_rule": "entry_vwap_dev<0 AND NOT(rise5<0 pullback)",
        "entry_guard_proxy": "vwap below without pullback",
        "match": lambda t: _match_a2(t),
    },
    {
        "cluster_id": "C12_core10_death60",
        "label": "Core10 + 60s<=-0.3%",
        "priority": 12,
        "guard_rule": "core10 AND loss_60s_0p3",
        "entry_guard_proxy": "Core10 immediate adverse move",
        "match": lambda t: _is_core10(t) and _bool(t.get("loss_60s_0p3")),
    },
    {
        "cluster_id": "C13_core10_residual",
        "label": "Core10 low-MFE residual",
        "priority": 13,
        "guard_rule": "core10 AND low_mfe_stop",
        "entry_guard_proxy": "Core10 residual",
        "match": lambda t: _is_core10(t) and _is_low_mfe_stop(t),
    },
    {
        "cluster_id": "C14_dyn40_residual",
        "label": "Dynamic40 low-MFE residual",
        "priority": 14,
        "guard_rule": "dynamic40 AND low_mfe_stop",
        "entry_guard_proxy": "Dynamic40 residual",
        "match": lambda t: _is_dynamic40(t) and _is_low_mfe_stop(t),
    },
]


def cluster_match(trade: Mapping[str, Any], cluster_id: str) -> bool:
    for spec in CLUSTER_SPECS:
        if spec["cluster_id"] == cluster_id:
            fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
            return bool(fn(trade))
    return False


def assign_death_cluster(trade: Mapping[str, Any]) -> str:
    if not _is_low_mfe_stop(trade):
        return ""
    for spec in sorted(CLUSTER_SPECS, key=lambda x: x["priority"]):
        fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
        if fn(trade):
            return str(spec["cluster_id"])
    return "C14_dyn40_residual"


def load_session_low_mfe_immediate_death(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "residual_trades": [], "all_production_enriched": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    events_path = sess_dir / "small_paper_events.csv"
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    if events_path.is_file():
        for row in _stream_events_csv(events_path):
            if row.get("event_type") == "accepted":
                accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    production = production_kept_trades(base)
    trade_keys = {(t.get("symbol", ""), t.get("entry_time", "")) for t in production}
    tick_paths = _build_tick_paths(events_path, trade_keys) if events_path.is_file() else {}

    all_enriched: list[dict[str, Any]] = []
    for trade in production:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_residual_trade(trade, acc)
        row.update(annotate_immediate_death(tick_paths.get(key, [])))
        row["pnl_pct"] = _float(row.get("pnl_pct") or trade.get("pnl_pct"))
        row["entry_price"] = _float(row.get("entry_price") or trade.get("entry_price"))
        all_enriched.append(row)

    low_mfe = [t for t in all_enriched if _is_low_mfe_stop(t)]
    low_mfe = _mark_symbol_reentry_clusters(low_mfe)
    for row in low_mfe:
        row["death_cluster"] = assign_death_cluster(row)
        row["residual_pattern"] = row.get("residual_pattern") or ""

    cluster_by_key = {(t.get("symbol", ""), t.get("entry_time", "")): t["death_cluster"] for t in low_mfe}
    reentry_by_key = {
        (t.get("symbol", ""), t.get("entry_time", "")): t.get("symbol_reentry_cluster", False)
        for t in low_mfe
    }
    for row in all_enriched:
        key = (row.get("symbol", ""), row.get("entry_time", ""))
        if key in cluster_by_key:
            row["death_cluster"] = cluster_by_key[key]
            row["symbol_reentry_cluster"] = reentry_by_key.get(key, False)

    return {
        **base,
        "residual_trades": low_mfe,
        "all_production_enriched": all_enriched,
        "residual_count": len(low_mfe),
        "error": "",
    }


def _counterfactual(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    cluster_id: str,
) -> dict[str, Any]:
    removed = [t for t in all_trades if cluster_match(t, cluster_id)]
    kept = [t for t in all_trades if not cluster_match(t, cluster_id)]
    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_trades]
    new_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
    removed_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed), 2)
    low_mfe_removed = [t for t in removed if _is_low_mfe_stop(t)]
    stop_removed = [t for t in removed if t.get("exit_reason_canonical") == "stop_hit"]
    return {
        "cluster_id": cluster_id,
        "removed_trades": len(removed),
        "skipped_pnl_actual": removed_pnl,
        "low_mfe_removed_count": len(low_mfe_removed),
        "low_mfe_removed_pnl_yen_100": round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in low_mfe_removed), 2
        ),
        "delta_yen": round(sum(new_yens) - sum(actual_yens), 2),
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


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase372LowMfeImmediateDeathForensic:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase372_low_mfe_immediate_death_summary.json",
            "clusters": self.reports_dir / "phase372_low_mfe_immediate_death_clusters.csv",
            "counterfactual": self.reports_dir / "phase372_low_mfe_immediate_death_counterfactual.csv",
            "by_symbol": self.reports_dir / "phase372_low_mfe_immediate_death_by_symbol.csv",
            "trades": self.reports_dir / "phase372_low_mfe_immediate_death_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _prepare(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        all_trades: list[dict[str, Any]] = []
        low_mfe: list[dict[str, Any]] = []
        for sr in self.session_results:
            for t in sr.get("all_production_enriched") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get("session_id")
                row["day_key"] = row.get("day_key") or sr.get("session_meta", {}).get("day_key")
                all_trades.append(row)
            for t in sr.get("residual_trades") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get("session_id")
                row["day_key"] = row.get("day_key") or sr.get("session_meta", {}).get("day_key")
                low_mfe.append(row)
        return all_trades, low_mfe

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        paths = self.paths()
        all_trades, low_mfe = self._prepare()

        total_loss = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in low_mfe), 2)
        cluster_rows: list[dict[str, Any]] = []
        cf_rows: list[dict[str, Any]] = []

        for spec in CLUSTER_SPECS:
            cid = str(spec["cluster_id"])
            subset = [t for t in low_mfe if t.get("death_cluster") == cid]
            guard_low_mfe = [t for t in low_mfe if cluster_match(t, cid)]
            yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset]
            total = round(sum(yens), 2) if yens else 0.0
            cf = _counterfactual(all_trades, cluster_id=cid)
            row = {
                "cluster_id": cid,
                "label": spec["label"],
                "guard_rule": spec["guard_rule"],
                "entry_guard_proxy": spec["entry_guard_proxy"],
                "priority": spec["priority"],
                "count": len(subset),
                "total_pnl_yen_100": total,
                "avg_pnl_yen_100": round(total / len(subset), 2) if subset else None,
                "profit_factor": _pf(yens),
                "guard_match_low_mfe_count": len(guard_low_mfe),
                "share_of_low_mfe_count": round(len(subset) / len(low_mfe), 4) if low_mfe else 0.0,
                "share_of_low_mfe_loss": round(total / total_loss, 4)
                if low_mfe and total < 0 and total_loss < 0
                else None,
                "dynamic40_count": sum(1 for t in subset if _is_dynamic40(t)),
                "core10_count": sum(1 for t in subset if _is_core10(t)),
                "death60_count": sum(1 for t in subset if _bool(t.get("loss_60s_0p3"))),
                "death120_count": sum(1 for t in subset if _bool(t.get("loss_120s_0p5"))),
                **cf,
            }
            cluster_rows.append(row)
            cf_rows.append(
                {
                    "cluster_id": cid,
                    "label": spec["label"],
                    "guard_rule": spec["guard_rule"],
                    "entry_guard_proxy": spec["entry_guard_proxy"],
                    "removed_trades": cf["removed_trades"],
                    "skipped_pnl_actual": cf["skipped_pnl_actual"],
                    "low_mfe_removed_count": cf["low_mfe_removed_count"],
                    "low_mfe_removed_pnl_yen_100": cf["low_mfe_removed_pnl_yen_100"],
                    "delta_yen": cf["delta_yen"],
                    "actual_pf": cf["actual_pf"],
                    "counterfactual_pf": cf["counterfactual_pf"],
                    "delta_pf": cf["delta_pf"],
                    "stop_hit_reduction_count": cf["stop_hit_reduction_count"],
                    "positive_delta": cf["delta_yen"] > 0,
                }
            )

        positive_cf = [r for r in cf_rows if r["positive_delta"]]
        positive_cf.sort(key=lambda r: r["delta_yen"], reverse=True)
        actionable_positive = [
            r
            for r in positive_cf
            if r["cluster_id"] not in RESIDUAL_CLUSTER_IDS
            and (r.get("skipped_pnl_actual") or 0) < 0
            and int(r.get("removed_trades") or 0) <= MAX_REMOVED_TRADES_ADOPT
        ]
        entry_proxy_positive = [
            r
            for r in actionable_positive
            if r["cluster_id"] not in FORENSIC_ONLY_CLUSTER_IDS
        ]
        worst_loss = min(cluster_rows, key=lambda r: r["total_pnl_yen_100"], default={})
        best_positive = actionable_positive[0] if actionable_positive else None
        best_entry_proxy = entry_proxy_positive[0] if entry_proxy_positive else None
        best_any = max(cf_rows, key=lambda r: r["delta_yen"])

        by_symbol = _by_symbol(low_mfe)
        axis = _axis_breakdown(low_mfe)

        adopt = False
        shadow = False
        if best_entry_proxy:
            adopt = (
                best_entry_proxy["delta_yen"] > 0
                and (best_entry_proxy.get("skipped_pnl_actual") or 0) < 0
                and best_entry_proxy["stop_hit_reduction_count"] > 0
                and best_entry_proxy["low_mfe_removed_count"] >= 5
            )
            shadow = best_entry_proxy["delta_yen"] > 0 and (
                not adopt or best_entry_proxy["cluster_id"] in PHASE368_FAILED_GUARDS
            )
            if best_entry_proxy["cluster_id"] in PHASE368_FAILED_GUARDS:
                adopt = False
                shadow = True

        summary = {
            "phase": 372,
            "title": "low_mfe_immediate_death_forensic",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_kept_low_mfe_stop_hit",
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "low_mfe_stop_count": len(low_mfe),
            "total_low_mfe_loss_yen_100": total_loss,
            "production_trade_count": len(all_trades),
            "immediate_death_axes": axis.get("immediate_death"),
            "axis_breakdown": axis,
            "clusters": {r["cluster_id"]: r for r in cluster_rows},
            "counterfactual_by_cluster": {r["cluster_id"]: r for r in cf_rows},
            "conclusion": {
                "largest_loss_cluster": worst_loss.get("cluster_id"),
                "largest_loss_yen_100": worst_loss.get("total_pnl_yen_100"),
                "positive_counterfactual_clusters": [r["cluster_id"] for r in positive_cf],
                "best_positive_cluster": best_positive["cluster_id"] if best_positive else None,
                "best_positive_delta_yen": best_positive["delta_yen"] if best_positive else None,
                "best_positive_delta_pf": best_positive["delta_pf"] if best_positive else None,
                "best_entry_proxy_cluster": best_entry_proxy["cluster_id"] if best_entry_proxy else None,
                "best_entry_proxy_delta_yen": best_entry_proxy["delta_yen"] if best_entry_proxy else None,
                "best_any_cluster": best_any["cluster_id"],
                "best_any_delta_yen": best_any["delta_yen"],
                "entry_guard_candidate": best_entry_proxy["cluster_id"] if best_entry_proxy else None,
                "expected_improvement_yen_100": best_entry_proxy["delta_yen"] if best_entry_proxy else None,
                "forensic_best_immediate_death_cluster": next(
                    (r["cluster_id"] for r in positive_cf if r["cluster_id"] in FORENSIC_ONLY_CLUSTER_IDS),
                    None,
                ),
                "dynamic40_largest_cluster": _largest_dyn_cluster(cluster_rows),
                "core10_largest_cluster": _largest_core_cluster(cluster_rows),
                "production_adopt_candidate": adopt,
                "shadow_validation_candidate": shadow or bool(best_entry_proxy),
                "recommendation": _recommendation(best_positive, best_entry_proxy, adopt, shadow),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if cluster_rows:
            _write_csv(
                paths["clusters"],
                cluster_rows,
                sorted({k for r in cluster_rows for k in r}),
            )
        if cf_rows:
            _write_csv(
                paths["counterfactual"],
                cf_rows,
                sorted({k for r in cf_rows for k in r}),
            )
        if by_symbol:
            _write_csv(
                paths["by_symbol"],
                by_symbol,
                [
                    "symbol",
                    "count",
                    "total_pnl_yen_100",
                    "avg_pnl_yen_100",
                    "death60_count",
                    "death120_count",
                    "dynamic40_count",
                    "core10_count",
                    "dominant_cluster",
                ],
            )
        if low_mfe:
            _write_csv(paths["trades"], low_mfe, TRADE_FIELDS)
        return paths


def _axis_breakdown(low_mfe: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def _axis(key: str) -> dict[str, Any]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_pnl_yen_100": 0.0}
        )
        for t in low_mfe:
            val = str(t.get(key) or "unknown")
            acc[val]["count"] += 1
            acc[val]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        return {
            k: {"count": v["count"], "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2)}
            for k, v in sorted(acc.items())
        }

    death60 = sum(1 for t in low_mfe if _bool(t.get("loss_60s_0p3")))
    death120 = sum(1 for t in low_mfe if _bool(t.get("loss_120s_0p5")))
    stop180 = sum(1 for t in low_mfe if _bool(t.get("stop_approach_180s")))
    return {
        "immediate_death": {
            "loss_60s_0p3_count": death60,
            "loss_120s_0p5_count": death120,
            "stop_approach_180s_count": stop180,
            "loss_60s_0p3_loss": round(
                sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in low_mfe
                    if _bool(t.get("loss_60s_0p3"))
                ),
                2,
            ),
            "loss_120s_0p5_loss": round(
                sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in low_mfe
                    if _bool(t.get("loss_120s_0p5"))
                ),
                2,
            ),
        },
        "universe_group": _axis("universe_group"),
        "session_kind": _axis("session_kind"),
        "entry_time_bucket": _axis("entry_time_bucket"),
        "board_dynamic_tier": _axis("board_dynamic_tier"),
        "symbol_reentry_cluster": _axis("symbol_reentry_cluster"),
    }


def _by_symbol(low_mfe: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "total_pnl_yen_100": 0.0,
            "death60": 0,
            "death120": 0,
            "dynamic40": 0,
            "core10": 0,
            "clusters": Counter(),
        }
    )
    for t in low_mfe:
        sym = str(t.get("symbol") or "")
        acc[sym]["count"] += 1
        acc[sym]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        if _bool(t.get("loss_60s_0p3")):
            acc[sym]["death60"] += 1
        if _bool(t.get("loss_120s_0p5")):
            acc[sym]["death120"] += 1
        if _is_dynamic40(t):
            acc[sym]["dynamic40"] += 1
        if _is_core10(t):
            acc[sym]["core10"] += 1
        acc[sym]["clusters"][str(t.get("death_cluster") or "")] += 1
    rows = []
    for sym, v in sorted(acc.items(), key=lambda x: x[1]["total_pnl_yen_100"]):
        rows.append(
            {
                "symbol": sym,
                "count": v["count"],
                "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                "avg_pnl_yen_100": round(v["total_pnl_yen_100"] / v["count"], 2),
                "death60_count": v["death60"],
                "death120_count": v["death120"],
                "dynamic40_count": v["dynamic40"],
                "core10_count": v["core10"],
                "dominant_cluster": v["clusters"].most_common(1)[0][0] if v["clusters"] else "",
            }
        )
    return rows


def _largest_dyn_cluster(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    dyn = [r for r in rows if int(r.get("dynamic40_count") or 0) > 0]
    return min(dyn, key=lambda r: r["total_pnl_yen_100"])["cluster_id"] if dyn else None


def _largest_core_cluster(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    core = [r for r in rows if int(r.get("core10_count") or 0) > 0]
    return min(core, key=lambda r: r["total_pnl_yen_100"])["cluster_id"] if core else None


def _recommendation(
    best_positive: Optional[Mapping[str, Any]],
    best_entry_proxy: Optional[Mapping[str, Any]],
    adopt: bool,
    shadow: bool,
) -> str:
    if adopt and best_entry_proxy:
        return (
            f"Production pilot candidate: {best_entry_proxy['cluster_id']} "
            f"(delta={best_entry_proxy['delta_yen']} yen)."
        )
    if shadow and best_entry_proxy:
        phase368_note = ""
        if best_entry_proxy["cluster_id"] in PHASE368_FAILED_GUARDS:
            phase368_note = " Phase368 shadow failed for similar reentry logic; treat as forensic-only uplift."
        return (
            f"Shadow-validate ENTRY guard proxy for {best_entry_proxy['cluster_id']} "
            f"(delta={best_entry_proxy['delta_yen']} yen).{phase368_note} "
            "Immediate-death timing clusters (C01-C03/C12) are forensic labels only."
        )
    if best_positive:
        return (
            f"Forensic uplift via {best_positive['cluster_id']} (delta={best_positive['delta_yen']} yen) "
            "is not directly implementable at ENTRY; use entry-time proxy shadow next."
        )
    return "No cluster shows positive counterfactual delta; do not pursue broad ENTRY guards."

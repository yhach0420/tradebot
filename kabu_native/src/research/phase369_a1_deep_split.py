"""
Phase369: A1 board-low low-momentum deep split with counterfactual clusters.
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

from research.phase366_stophit_reclassification import MIN_DAY
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
    _match_a4,
    _pf,
    enrich_residual_trade,
    load_session_residual_forensic,
)
from research.phase360_eother_classification import entry_time_bucket

A1_PATTERN_ID = "A1_board_low_low_momentum"
JST = ZoneInfo("Asia/Tokyo")

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
    "peak_mfe_pct",
    "exit_reason_canonical",
    "a1_cluster",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "entry_momentum_score",
    "day_high_distance_pct",
    "entry_imbalance_percentile",
    "board_dynamic_tier",
    "trading_value",
    "turnover_proxy",
    "phase355_would_block",
    "phase364_would_block",
    "prior_low_mfe_stop_same_day",
    "symbol_reentry_cluster",
    "stop_hit_chain_index",
]


def _is_dynamic40(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_group") or "") == "dynamic40"


def _is_core10(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_group") or "") == "core10"


def _is_early_open(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("entry_time_bucket") or "") == "09:00-09:30"


def _is_near_day_high(trade: Mapping[str, Any], threshold: float = 1.5) -> bool:
    dist = _float(trade.get("day_high_distance_pct"))
    return dist is not None and dist <= threshold


def _is_negative_rise(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    return rise5 is not None and rise5 < 0


def _is_flat_rise(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    return rise5 is not None and 0 <= rise5 < 0.3


def _is_pm_late(trade: Mapping[str, Any]) -> bool:
    bucket = str(trade.get("entry_time_bucket") or "")
    sk = str(trade.get("session_kind") or "")
    return sk == "pm" and bucket in LATE_TIME_BUCKETS


def _is_very_low_imbalance(trade: Mapping[str, Any], threshold: float = 15.0) -> bool:
    pctile = _float(trade.get("entry_imbalance_percentile"))
    return pctile is not None and pctile < threshold


def _is_negative_vwap_not_pullback(trade: Mapping[str, Any]) -> bool:
    vwap = _float(trade.get("entry_vwap_dev_pct"))
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    if vwap is None or vwap >= 0:
        return False
    if rise5 is not None and rise5 < 0:
        return False
    return True


CLUSTER_SPECS: list[dict[str, Any]] = [
    {
        "cluster_id": "K01_dyn40_early_open",
        "label": "Dynamic40 + 09:00-09:30 + A1",
        "priority": 1,
        "guard_rule": "board_low AND mom<0.30 AND dynamic40 AND entry 09:00-09:30",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_early_open(t),
    },
    {
        "cluster_id": "K02_dyn40_near_day_high",
        "label": "Dynamic40 + day_high<=1.5% + A1",
        "priority": 2,
        "guard_rule": "A1 AND dynamic40 AND day_high_distance_pct<=1.5",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_near_day_high(t),
    },
    {
        "cluster_id": "K03_dyn40_negative_rise",
        "label": "Dynamic40 + rise5<0 + A1 (not Phase355 pullback)",
        "priority": 3,
        "guard_rule": "A1 AND dynamic40 AND entry_rise_5min_pct<0",
        "match": lambda t: _match_a1(t)
        and _is_dynamic40(t)
        and _is_negative_rise(t)
        and not _bool(t.get("phase355_would_block")),
    },
    {
        "cluster_id": "K04_dyn40_flat_rise",
        "label": "Dynamic40 + 0<=rise5<0.3% + A1",
        "priority": 4,
        "guard_rule": "A1 AND dynamic40 AND 0<=entry_rise_5min<0.3",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_flat_rise(t),
    },
    {
        "cluster_id": "K05_dyn40_vwap_below_not_pullback",
        "label": "Dynamic40 + vwap<0 outside pullback + A1",
        "priority": 5,
        "guard_rule": "A1 AND dynamic40 AND vwap_dev<0 AND NOT(rise5<0)",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_negative_vwap_not_pullback(t),
    },
    {
        "cluster_id": "K06_dyn40_pm_late",
        "label": "Dynamic40 + PM late bucket + A1",
        "priority": 6,
        "guard_rule": "A1 AND dynamic40 AND PM late session bucket",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_pm_late(t),
    },
    {
        "cluster_id": "K07_dyn40_low_liquidity",
        "label": "Dynamic40 + low liquidity + A1",
        "priority": 7,
        "guard_rule": "A1 AND dynamic40 AND (tv<1e8 OR turnover<0.002)",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _match_a4(t),
    },
    {
        "cluster_id": "K08_dyn40_phase364_overlap",
        "label": "Dynamic40 + Phase364 would block + A1",
        "priority": 8,
        "guard_rule": "A1 AND dynamic40 AND phase364_would_block",
        "match": lambda t: _match_a1(t)
        and _is_dynamic40(t)
        and _bool(t.get("phase364_would_block")),
    },
    {
        "cluster_id": "K09_dyn40_symbol_repeat",
        "label": "Dynamic40 + same-day symbol repeat low-MFE stop + A1",
        "priority": 9,
        "guard_rule": "A1 AND dynamic40 AND symbol_reentry_cluster",
        "match": lambda t: _match_a1(t)
        and _is_dynamic40(t)
        and _bool(t.get("symbol_reentry_cluster")),
    },
    {
        "cluster_id": "K10_dyn40_stop_chain",
        "label": "Dynamic40 + prior low-MFE stop chain + A1",
        "priority": 10,
        "guard_rule": "A1 AND dynamic40 AND prior_low_mfe_stop_same_day",
        "match": lambda t: _match_a1(t)
        and _is_dynamic40(t)
        and _bool(t.get("prior_low_mfe_stop_same_day")),
    },
    {
        "cluster_id": "K11_dyn40_very_low_imbalance",
        "label": "Dynamic40 + imb_pctile<15 + A1",
        "priority": 11,
        "guard_rule": "A1 AND dynamic40 AND entry_imbalance_percentile<15",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t) and _is_very_low_imbalance(t),
    },
    {
        "cluster_id": "K12_core10_early_open",
        "label": "Core10 + 09:00-09:30 + A1",
        "priority": 12,
        "guard_rule": "A1 AND core10 AND entry 09:00-09:30",
        "match": lambda t: _match_a1(t) and _is_core10(t) and _is_early_open(t),
    },
    {
        "cluster_id": "K13_core10_other",
        "label": "Core10 A1 residual",
        "priority": 13,
        "guard_rule": "A1 AND core10",
        "match": lambda t: _match_a1(t) and _is_core10(t),
    },
    {
        "cluster_id": "K14_dyn40_residual",
        "label": "Dynamic40 A1 residual",
        "priority": 14,
        "guard_rule": "A1 AND dynamic40",
        "match": lambda t: _match_a1(t) and _is_dynamic40(t),
    },
]

CLUSTER_IDS = tuple(s["cluster_id"] for s in CLUSTER_SPECS)


def cluster_match(trade: Mapping[str, Any], cluster_id: str) -> bool:
    for spec in CLUSTER_SPECS:
        if spec["cluster_id"] == cluster_id:
            fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
            return bool(fn(trade))
    return False


def assign_a1_cluster(trade: Mapping[str, Any]) -> str:
    if not _match_a1(trade):
        return ""
    for spec in sorted(CLUSTER_SPECS, key=lambda x: x["priority"]):
        fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
        if fn(trade):
            return str(spec["cluster_id"])
    return "K14_dyn40_residual"


def annotate_low_mfe_stop_chain(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Chronological day context on low-MFE stops: prior stop same symbol/day."""
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day_key") or "")].append(dict(t))

    out: list[dict[str, Any]] = []
    for _day, rows in by_day.items():
        rows.sort(key=lambda r: str(r.get("entry_time") or ""))
        day_stopped: set[str] = set()
        for row in rows:
            sym = str(row.get("symbol") or "")
            row["prior_low_mfe_stop_same_day"] = sym in day_stopped
            if not row.get("entry_time_bucket"):
                row["entry_time_bucket"] = entry_time_bucket(str(row.get("entry_time") or ""))
            out.append(row)
            if sym:
                day_stopped.add(sym)
    return out


def _is_a1_low_mfe_stop(trade: Mapping[str, Any]) -> bool:
    return (
        _is_low_mfe_stop(trade)
        and str(trade.get("residual_pattern") or "") == A1_PATTERN_ID
    )


def _counterfactual(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    cluster_id: str,
) -> dict[str, Any]:
    removed = [t for t in all_trades if cluster_match(t, cluster_id)]
    kept = [t for t in all_trades if not cluster_match(t, cluster_id)]
    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_trades]
    new_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
    a1_removed = [t for t in removed if _is_a1_low_mfe_stop(t)]
    return {
        "cluster_id": cluster_id,
        "removed_trades": len(removed),
        "removed_pnl_yen_100": round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed), 2
        ),
        "a1_removed_count": len(a1_removed),
        "a1_removed_pnl_yen_100": round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in a1_removed), 2
        ),
        "counterfactual_delta_yen": round(sum(new_yens) - sum(actual_yens), 2),
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
        "stop_hit_reduction_count": sum(
            1 for t in all_trades if t.get("exit_reason_canonical") == "stop_hit"
        )
        - sum(1 for t in kept if t.get("exit_reason_canonical") == "stop_hit"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase369A1DeepSplit:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase369_a1_deep_split_summary.json",
            "clusters": self.reports_dir / "phase369_a1_clusters.csv",
            "counterfactual": self.reports_dir / "phase369_a1_counterfactual.csv",
            "trades": self.reports_dir / "phase369_a1_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _prepare_trades(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        all_raw: list[dict[str, Any]] = []
        low_mfe_raw: list[dict[str, Any]] = []
        for sr in self.session_results:
            for t in sr.get("all_production_enriched") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get(
                    "session_id"
                )
                row["day_key"] = row.get("day_key") or sr.get("session_meta", {}).get("day_key")
                all_raw.append(row)
            for t in sr.get("residual_trades") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get(
                    "session_id"
                )
                row["day_key"] = row.get("day_key") or sr.get("session_meta", {}).get("day_key")
                low_mfe_raw.append(row)

        low_mfe_annotated = annotate_low_mfe_stop_chain(low_mfe_raw)
        chain_by_key = {
            (t.get("symbol", ""), t.get("entry_time", "")): t.get("prior_low_mfe_stop_same_day")
            for t in low_mfe_annotated
        }
        for row in all_raw:
            key = (row.get("symbol", ""), row.get("entry_time", ""))
            if key in chain_by_key:
                row["prior_low_mfe_stop_same_day"] = chain_by_key[key]

        a1_trades = [t for t in low_mfe_annotated if _is_a1_low_mfe_stop(t)]
        for row in a1_trades:
            row["a1_cluster"] = assign_a1_cluster(row)
            row["stop_hit_chain_index"] = row.get("symbol_day_stop_index")
        return all_raw, a1_trades

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        paths = self.paths()
        all_trades, a1_trades = self._prepare_trades()

        cluster_rows: list[dict[str, Any]] = []
        cf_rows: list[dict[str, Any]] = []
        axis_breakdown: dict[str, dict[str, Any]] = {}

        for spec in CLUSTER_SPECS:
            cid = str(spec["cluster_id"])
            subset = [t for t in a1_trades if t.get("a1_cluster") == cid]
            guard_a1 = [t for t in a1_trades if cluster_match(t, cid)]
            yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in subset]
            guard_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in guard_a1]
            total = round(sum(yens), 2) if yens else 0.0
            guard_total = round(sum(guard_yens), 2) if guard_yens else 0.0
            cf = _counterfactual(all_trades, cluster_id=cid)
            row = {
                "cluster_id": cid,
                "label": spec["label"],
                "guard_rule": spec["guard_rule"],
                "priority": spec["priority"],
                "count": len(subset),
                "total_pnl_yen_100": total,
                "avg_pnl_yen_100": round(total / len(subset), 2) if subset else None,
                "profit_factor": _pf(yens),
                "guard_match_a1_count": len(guard_a1),
                "guard_match_a1_total_pnl_yen_100": guard_total,
                "guard_match_a1_avg_pnl_yen_100": round(guard_total / len(guard_a1), 2)
                if guard_a1
                else None,
                "dynamic40_count": sum(1 for t in subset if _is_dynamic40(t)),
                "core10_count": sum(1 for t in subset if _is_core10(t)),
                "share_of_a1_count": round(len(subset) / len(a1_trades), 4) if a1_trades else 0.0,
                "share_of_a1_loss": round(total / sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in a1_trades), 4)
                if a1_trades and total < 0
                else None,
                **cf,
            }
            cluster_rows.append(row)
            cf_rows.append(
                {
                    "cluster_id": cid,
                    "label": spec["label"],
                    "guard_rule": spec["guard_rule"],
                    "removed_trades": cf["removed_trades"],
                    "a1_removed_count": cf["a1_removed_count"],
                    "removed_pnl_yen_100": cf["removed_pnl_yen_100"],
                    "a1_removed_pnl_yen_100": cf["a1_removed_pnl_yen_100"],
                    "counterfactual_delta_yen": cf["counterfactual_delta_yen"],
                    "actual_pf": cf["actual_pf"],
                    "counterfactual_pf": cf["counterfactual_pf"],
                    "delta_pf": cf["delta_pf"],
                    "stop_hit_reduction_count": cf["stop_hit_reduction_count"],
                    "positive_delta": cf["counterfactual_delta_yen"] > 0,
                }
            )

        positive_cf = [r for r in cf_rows if r["positive_delta"]]
        positive_cf.sort(key=lambda r: r["counterfactual_delta_yen"], reverse=True)
        best_positive = positive_cf[0] if positive_cf else None
        best_any = max(cf_rows, key=lambda r: r["counterfactual_delta_yen"])

        def _axis_stats(trades: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
            acc: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"count": 0, "total_pnl_yen_100": 0.0}
            )
            for t in trades:
                val = str(t.get(key) or "unknown")
                acc[val]["count"] += 1
                acc[val]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            return {
                k: {
                    "count": v["count"],
                    "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                }
                for k, v in sorted(acc.items())
            }

        axis_breakdown = {
            "universe_group": _axis_stats(a1_trades, "universe_group"),
            "session_kind": _axis_stats(a1_trades, "session_kind"),
            "entry_time_bucket": _axis_stats(a1_trades, "entry_time_bucket"),
            "phase364_would_block": _axis_stats(
                a1_trades, "phase364_would_block"
            ),
            "prior_low_mfe_stop_same_day": _axis_stats(
                a1_trades, "prior_low_mfe_stop_same_day"
            ),
            "symbol_reentry_cluster": _axis_stats(a1_trades, "symbol_reentry_cluster"),
        }

        total_a1_loss = round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in a1_trades), 2
        )

        summary = {
            "phase": 369,
            "title": "a1_board_low_low_momentum_deep_split",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_kept_low_mfe_stop_hit_a1_board_low_low_momentum",
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "a1_trade_count": len(a1_trades),
            "a1_total_loss_yen_100": total_a1_loss,
            "cluster_count": len(CLUSTER_SPECS),
            "clusters": {r["cluster_id"]: r for r in cluster_rows},
            "counterfactual_by_cluster": {r["cluster_id"]: r for r in cf_rows},
            "axis_breakdown": axis_breakdown,
            "conclusion": {
                "positive_counterfactual_clusters": [r["cluster_id"] for r in positive_cf],
                "best_positive_cluster": best_positive["cluster_id"] if best_positive else None,
                "best_positive_delta_yen": best_positive["counterfactual_delta_yen"]
                if best_positive
                else None,
                "best_positive_delta_pf": best_positive["delta_pf"] if best_positive else None,
                "best_any_cluster": best_any["cluster_id"],
                "best_any_delta_yen": best_any["counterfactual_delta_yen"],
                "shadow_candidate": best_positive["cluster_id"] if best_positive else None,
                "recommendation": (
                    f"Shadow-validate {best_positive['cluster_id']} "
                    f"(delta={best_positive['counterfactual_delta_yen']} yen)."
                    if best_positive
                    else "No A1 sub-cluster shows positive counterfactual delta; "
                    "do not pursue A1-based ENTRY guard."
                ),
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
        if a1_trades:
            _write_csv(paths["trades"], a1_trades, TRADE_FIELDS)
        return paths

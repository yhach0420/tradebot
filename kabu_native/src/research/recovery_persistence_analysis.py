"""
Phase 30: recovery persistence analysis from enriched trade rows.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import MOMENTUM_V2_REFERENCE

PERSIST_HORIZONS = (15, 30, 60, 90, 180)


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for r in rows if r.get(key)) / len(rows)


def _dist(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "p50": None, "mean": None}
    s = sorted(vals)
    return {"count": len(s), "p50": statistics.median(s), "mean": statistics.mean(s)}


def _horizon_compare(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _field(grp: Sequence[Mapping[str, Any]], key: str) -> list[float]:
        out: list[float] = []
        for r in grp:
            v = _as_float(r.get(key))
            if v is not None:
                out.append(v)
        return out

    horizons: dict[str, Any] = {}
    for h in PERSIST_HORIZONS:
        horizons[f"{h}s"] = {
            "momentum_persistence": {
                "winners": _dist(_field(winners, f"early_{h}s_momentum_pct_from_entry")),
                "losers": _dist(_field(losers, f"early_{h}s_momentum_pct_from_entry")),
            },
            "vwap_persistence": {
                "winners": _dist(_field(winners, f"early_{h}s_vwap_distance_change")),
                "losers": _dist(_field(losers, f"early_{h}s_vwap_distance_change")),
            },
            "favorable_persistence": {
                "winners": _dist(_field(winners, f"early_{h}s_max_favorable_pct")),
                "losers": _dist(_field(losers, f"early_{h}s_max_favorable_pct")),
            },
            "imbalance_persistence": {
                "winners": _dist(_field(winners, f"early_{h}s_board_imbalance_change")),
                "losers": _dist(_field(losers, f"early_{h}s_board_imbalance_change")),
            },
        }

    return {"label": label, "trade_count": len(rows), "winners": len(winners), "losers": len(losers), "horizons": horizons}


def build_recovery_persistence_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    focus_profiles_prefix: str = "momentum_volume_v8_",
) -> dict[str, Any]:
    rows = list(trade_rows)
    v8_rows = [r for r in rows if str(r.get("profile", "")).startswith(focus_profiles_prefix)]
    ref_rows = [r for r in rows if str(r.get("profile")) == MOMENTUM_V2_REFERENCE]
    analysis_rows = v8_rows if v8_rows else ref_rows
    n = len(analysis_rows)

    return {
        "phase": 30,
        "trade_count": n,
        "aggregate_rates": {
            "reclaim_persistence_rate": _rate(analysis_rows, "reclaim_persistent"),
            "reclaim_failure_rate": _rate(analysis_rows, "reclaim_failure_persistent"),
            "favorable_persistence_rate": _rate(analysis_rows, "favorable_persistent"),
            "favorable_fade_rate": _rate(analysis_rows, "favorable_fade"),
            "adverse_persistence_rate": (
                sum(1 for r in analysis_rows if (_as_float(r.get("adverse_persistence_count")) or 0) >= 4) / n
                if n
                else None
            ),
            "imbalance_persistence_rate": (
                sum(1 for r in analysis_rows if (_as_float(r.get("imbalance_persistence_ticks")) or 0) >= 5) / n
                if n
                else None
            ),
            "recovery_then_fail_rate": _rate(analysis_rows, "recovery_then_fail"),
            "recovery_then_trend_rate": _rate(analysis_rows, "recovery_then_trend"),
        },
        "winner_loser_v2_reference": _horizon_compare(ref_rows, MOMENTUM_V2_REFERENCE),
        "winner_loser_v8_focus": _horizon_compare(analysis_rows, "v8_or_reference"),
        "by_outcome": {
            "recovery_then_trend": {
                "count": sum(1 for r in analysis_rows if r.get("recovery_then_trend")),
                "avg_pnl_pct": (
                    statistics.mean(
                        float(r["pnl_pct"])
                        for r in analysis_rows
                        if r.get("recovery_then_trend")
                    )
                    if any(r.get("recovery_then_trend") for r in analysis_rows)
                    else None
                ),
            },
            "recovery_then_fail": {
                "count": sum(1 for r in analysis_rows if r.get("recovery_then_fail")),
                "avg_pnl_pct": (
                    statistics.mean(
                        float(r["pnl_pct"]) for r in analysis_rows if r.get("recovery_then_fail")
                    )
                    if any(r.get("recovery_then_fail") for r in analysis_rows)
                    else None
                ),
            },
            "reclaim_persistent": {
                "count": sum(1 for r in analysis_rows if r.get("reclaim_persistent")),
                "avg_pnl_pct": (
                    statistics.mean(
                        float(r["pnl_pct"]) for r in analysis_rows if r.get("reclaim_persistent")
                    )
                    if any(r.get("reclaim_persistent") for r in analysis_rows)
                    else None
                ),
            },
            "favorable_fade": {
                "count": sum(1 for r in analysis_rows if r.get("favorable_fade")),
                "avg_pnl_pct": (
                    statistics.mean(float(r["pnl_pct"]) for r in analysis_rows if r.get("favorable_fade"))
                    if any(r.get("favorable_fade") for r in analysis_rows)
                    else None
                ),
            },
        },
        "temporary_vs_sustained": {
            "note": "reclaim_persistent + recovery_then_trend = sustained; reclaim_failure + favorable_fade = temporary",
            "sustained_recovery_rate": (
                sum(
                    1
                    for r in analysis_rows
                    if r.get("reclaim_persistent") or r.get("recovery_then_trend")
                )
                / n
                if n
                else None
            ),
            "temporary_bounce_rate": (
                sum(
                    1
                    for r in analysis_rows
                    if r.get("favorable_fade") or r.get("recovery_then_fail")
                )
                / n
                if n
                else None
            ),
        },
    }

"""
Phase 32: state transition path analysis from enriched trades.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Mapping, Optional, Sequence


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


def build_state_transition_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    focus_prefix: str = "momentum_volume_v10_",
) -> dict[str, Any]:
    rows = [r for r in trade_rows if str(r.get("profile", "")).startswith(focus_prefix)]
    if not rows:
        rows = list(trade_rows)
    n = len(rows)

    path_counter: Counter[str] = Counter()
    for r in rows:
        freq = r.get("transition_path_frequency")
        if isinstance(freq, dict):
            for k, v in freq.items():
                path_counter[str(k)] += int(v)
        for p in r.get("transition_paths") or []:
            path_counter[str(p)] += 1

    velocities = [
        _as_float(r.get("bullish_to_bearish_velocity_ticks"))
        for r in rows
        if _as_float(r.get("bullish_to_bearish_velocity_ticks")) is not None
    ]

    recovery_dur = [
        float(r.get("recovery_transition_score") or 0)
        for r in rows
        if r.get("recovery_transition_complete")
    ]
    collapse_dur = [
        float(r.get("max_bearish_duration_ticks") or 0)
        for r in rows
        if r.get("collapse_transition_ready")
    ]
    neutral_dur = [_as_float(r.get("max_neutral_duration_ticks")) for r in rows]
    neutral_dur = [x for x in neutral_dur if x is not None]

    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _wl(key: str) -> dict[str, Any]:
        wv = [_as_float(r.get(key)) for r in winners]
        lv = [_as_float(r.get(key)) for r in losers]
        wv = [x for x in wv if x is not None]
        lv = [x for x in lv if x is not None]
        return {
            "winners": _dist(wv),
            "losers": _dist(lv),
            "winner_minus_loser_mean": (
                statistics.mean(wv) - statistics.mean(lv) if wv and lv else None
            ),
        }

    return {
        "phase": 32,
        "trade_count": n,
        "transition_path_frequency": dict(path_counter),
        "aggregate_rates": {
            "recovery_transition_success_rate": _rate(rows, "recovery_transition_complete"),
            "recovery_transition_active_rate": _rate(rows, "recovery_transition_active"),
            "collapse_transition_rate": _rate(rows, "collapse_transition_ready"),
            "bearish_locked_rate": _rate(rows, "bearish_locked"),
            "recovery_transition_failure_rate": _rate(rows, "recovery_transition_failure"),
            "fixed_time_proxy_rate": _rate(rows, "fixed_time_proxy_fired"),
        },
        "durations_ticks": {
            "bullish_to_bearish_velocity": _dist(velocities),
            "recovery_transition_score": _dist(recovery_dur),
            "collapse_bearish_duration": _dist(collapse_dur),
            "neutral_stabilization": _dist(neutral_dur),
        },
        "winner_loser_comparison": {
            "recovery_transition_score": _wl("recovery_transition_score"),
            "collapse_transition_score": _wl("collapse_transition_score"),
            "max_bearish_duration_ticks": _wl("max_bearish_duration_ticks"),
            "max_bullish_duration_ticks": _wl("max_bullish_duration_ticks"),
            "bullish_to_bearish_velocity_ticks": _wl("bullish_to_bearish_velocity_ticks"),
        },
        "transition_exit_breakdown": {
            "collapse": sum(
                1 for r in rows if str(r.get("exit_reason")) == "transition_collapse_exit"
            ),
            "recovery_failure": sum(
                1
                for r in rows
                if str(r.get("exit_reason")) == "transition_recovery_failure_exit"
            ),
            "bearish_continuation": sum(
                1
                for r in rows
                if str(r.get("exit_reason")) == "transition_bearish_continuation_exit"
            ),
        },
    }

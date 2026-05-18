"""
Phase 33: duration-weighted persistence analysis from enriched trades.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _dist(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "p50": None, "mean": None}
    s = sorted(vals)
    return {"count": len(s), "p50": statistics.median(s), "mean": statistics.mean(s)}


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for r in rows if r.get(key)) / len(rows)


def build_duration_weight_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    focus_prefix: str = "momentum_volume_v11_",
) -> dict[str, Any]:
    rows = [r for r in trade_rows if str(r.get("profile", "")).startswith(focus_prefix)]
    if not rows:
        rows = list(trade_rows)
    n = len(rows)

    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _vals(grp: Sequence[Mapping[str, Any]], key: str) -> list[float]:
        out: list[float] = []
        for r in grp:
            v = _as_float(r.get(key))
            if v is not None:
                out.append(v)
        return out

    def _wl(key: str) -> dict[str, Any]:
        wv, lv = _vals(winners, key), _vals(losers, key)
        return {
            "winners": _dist(wv),
            "losers": _dist(lv),
            "winner_minus_loser_mean": (
                statistics.mean(wv) - statistics.mean(lv) if wv and lv else None
            ),
        }

    held = [r for r in rows if int(r.get("weighted_hold_events") or 0) > 0]
    held_win = sum(1 for r in held if float(r.get("pnl_pct", 0)) > 0)
    held_loss = sum(1 for r in held if float(r.get("pnl_pct", 0)) <= 0)

    return {
        "phase": 33,
        "trade_count": n,
        "distributions": {
            "bullish_weighted_score": _wl("bullish_weighted_score"),
            "bearish_weighted_score": _wl("bearish_weighted_score"),
            "max_bullish_duration_ticks": _wl("max_bullish_duration_ticks"),
            "max_bearish_duration_ticks": _wl("max_bearish_duration_ticks"),
            "collapse_weighted": _wl("collapse_weighted"),
        },
        "aggregate_rates": {
            "bullish_decay_rate": _rate(rows, "bullish_decay_detected"),
            "collapse_weighted_rate": _rate(rows, "collapse_weighted_ready"),
            "structure_break_weighted_rate": _rate(rows, "structure_break_weighted_ready"),
            "short_bearish_noise_rate": _rate(rows, "short_bearish_noise"),
            "weighted_hold_success_rate": (held_win / len(held)) if held else None,
            "weighted_false_hold_rate": (held_loss / len(held)) if held else None,
            "fixed_time_proxy_rate": _rate(rows, "fixed_time_proxy_fired"),
        },
        "duration_patterns": {
            "neutral_stabilization": _wl("neutral_stabilization_weight"),
            "reclaim_weighted": _wl("reclaim_weighted"),
            "favorable_weighted": _wl("favorable_weighted"),
        },
        "exit_breakdown": {
            "weighted_bearish": sum(
                1 for r in rows if str(r.get("exit_reason")) == "weighted_bearish_persistence_exit"
            ),
            "weighted_collapse": sum(
                1 for r in rows if str(r.get("exit_reason")) == "weighted_collapse_continuation_exit"
            ),
            "weighted_decay": sum(
                1 for r in rows if str(r.get("exit_reason")) == "weighted_bullish_decay_exit"
            ),
            "weighted_structure": sum(
                1 for r in rows if str(r.get("exit_reason")) == "weighted_structure_break_exit"
            ),
        },
    }

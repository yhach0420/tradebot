"""
Phase 35: continuation momentum analysis from enriched trades.
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


def build_continuation_momentum_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    focus_prefix: str = "momentum_volume_v13_",
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

    held = [r for r in rows if int(r.get("momentum_hold_events") or 0) > 0]
    held_win = sum(1 for r in held if float(r.get("pnl_pct", 0)) > 0)
    held_loss = sum(1 for r in held if float(r.get("pnl_pct", 0)) <= 0)

    weakness = sum(1 for r in rows if int(r.get("continuation_weakness_ticks") or 0) >= 4)

    return {
        "phase": 35,
        "trade_count": n,
        "distributions": {
            "momentum_continuation_score": _wl("momentum_continuation_score"),
            "max_momentum_continuation_duration": _wl("max_momentum_continuation_duration"),
            "favorable_continuation": _wl("favorable_continuation"),
            "positive_momentum_persistence": _wl("positive_momentum_persistence"),
            "bearish_accumulation_score": _wl("bearish_accumulation_score"),
        },
        "aggregate_rates": {
            "momentum_decay_rate": _rate(rows, "momentum_decay_detected"),
            "continuation_recovery_rate": _rate(rows, "continuation_recovery_detected"),
            "continuation_weakness_rate": (weakness / n) if n else None,
            "bearish_accumulation_rate": (
                sum(1 for r in rows if int(r.get("bearish_accumulation_ticks") or 0) >= 4) / n
                if n
                else None
            ),
            "continuation_hold_success_rate": (held_win / len(held)) if held else None,
            "continuation_false_hold_rate": (held_loss / len(held)) if held else None,
            "fixed_time_proxy_rate": _rate(rows, "fixed_time_proxy_fired"),
        },
        "persistence_patterns": {
            "favorable_fade_persistence": _wl("favorable_fade_persistence"),
            "bullish_duration_component": _wl("bullish_duration_component"),
        },
        "exit_breakdown": {
            "momentum_continuation_loss": sum(
                1 for r in rows if str(r.get("exit_reason")) == "momentum_continuation_loss_exit"
            ),
            "momentum_decay": sum(
                1 for r in rows if str(r.get("exit_reason")) == "momentum_decay_persistence_exit"
            ),
            "bearish_accumulation": sum(
                1
                for r in rows
                if str(r.get("exit_reason")) == "bearish_accumulation_persistence_exit"
            ),
            "continuation_weakness": sum(
                1
                for r in rows
                if str(r.get("exit_reason")) == "continuation_weakness_accumulation_exit"
            ),
        },
    }

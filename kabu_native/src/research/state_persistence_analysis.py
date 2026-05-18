"""
Phase 31: state persistence analysis from enriched trade rows.
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


def build_state_persistence_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    focus_prefix: str = "momentum_volume_v9_",
) -> dict[str, Any]:
    rows = [r for r in trade_rows if str(r.get("profile", "")).startswith(focus_prefix)]
    if not rows:
        rows = list(trade_rows)
    n = len(rows)

    bull_scores = [_as_float(r.get("bullish_instant_score")) for r in rows]
    bear_scores = [_as_float(r.get("bearish_instant_score")) for r in rows]
    bull_scores = [x for x in bull_scores if x is not None]
    bear_scores = [x for x in bear_scores if x is not None]

    trans_counter: Counter[str] = Counter()
    for r in rows:
        paths = r.get("state_transition_paths") or []
        if isinstance(paths, list):
            for p in paths:
                trans_counter[str(p)] += 1

    bear_dur = [_as_float(r.get("max_bearish_persist_ticks")) for r in rows]
    bull_dur = [_as_float(r.get("max_bullish_persist_ticks")) for r in rows]
    rec_dur = [_as_float(r.get("recovery_persist_ticks")) for r in rows]
    bear_dur = [x for x in bear_dur if x is not None]
    bull_dur = [x for x in bull_dur if x is not None]
    rec_dur = [x for x in rec_dur if x is not None]

    state_exits = [
        r
        for r in rows
        if str(r.get("exit_reason", "")).startswith("state_")
    ]
    fixed_proxy = sum(1 for r in rows if r.get("fixed_time_proxy_fired"))

    return {
        "phase": 31,
        "trade_count": n,
        "persistence_score_distribution": {
            "bullish_instant": _dist(bull_scores),
            "bearish_instant": _dist(bear_scores),
        },
        "aggregate_rates": {
            "bullish_to_bearish_transition_rate": (
                sum(int(r.get("bullish_to_bearish_transitions") or 0) > 0 for r in rows) / n if n else None
            ),
            "reclaim_persistence_rate": _rate(rows, "reclaim_persistent"),
            "favorable_persistence_rate": _rate(rows, "favorable_persistent"),
            "recovery_then_trend_rate": _rate(rows, "recovery_then_trend"),
            "recovery_then_fail_rate": _rate(rows, "recovery_then_fail"),
            "state_based_exit_rate": (len(state_exits) / n) if n else None,
            "fixed_time_proxy_rate": (fixed_proxy / n) if n else None,
        },
        "persistence_durations_ticks": {
            "bearish_persistence": _dist(bear_dur),
            "bullish_persistence": _dist(bull_dur),
            "recovery_persistence": _dist(rec_dur),
        },
        "state_transition_paths": dict(trans_counter),
        "time_vs_state": {
            "note": "v9 uses eval-tick persistence; fixed_time_proxy fires if bearish at tick~12 (~legacy 60s window proxy)",
            "state_exit_count": len(state_exits),
            "fixed_time_proxy_count": fixed_proxy,
            "fixed_time_dependency_ratio": (fixed_proxy / max(len(state_exits), 1)) if state_exits else None,
        },
        "winner_loser_by_dominant": _winner_loser_split(rows),
    }


def _winner_loser_split(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _mean(grp: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
        vals = [_as_float(r.get(key)) for r in grp]
        vals = [v for v in vals if v is not None]
        return statistics.mean(vals) if vals else None

    horizons = (15, 30, 60, 90, 180)
    hblock: dict[str, Any] = {}
    for h in horizons:
        hblock[f"{h}s"] = {
            "winners_momentum": _mean(winners, f"early_{h}s_momentum_pct_from_entry"),
            "losers_momentum": _mean(losers, f"early_{h}s_momentum_pct_from_entry"),
            "winners_vwap": _mean(winners, f"early_{h}s_vwap_distance_change"),
            "losers_vwap": _mean(losers, f"early_{h}s_vwap_distance_change"),
            "winners_favorable": _mean(winners, f"early_{h}s_max_favorable_pct"),
            "losers_favorable": _mean(losers, f"early_{h}s_max_favorable_pct"),
            "winners_imbalance": _mean(winners, f"early_{h}s_board_imbalance_change"),
            "losers_imbalance": _mean(losers, f"early_{h}s_board_imbalance_change"),
        }

    return {
        "winners": len(winners),
        "losers": len(losers),
        "winners_avg_bullish_score": _mean(winners, "bullish_instant_score"),
        "losers_avg_bullish_score": _mean(losers, "bullish_instant_score"),
        "winners_avg_bearish_score": _mean(winners, "bearish_instant_score"),
        "losers_avg_bearish_score": _mean(losers, "bearish_instant_score"),
        "horizons": hblock,
    }

"""
Phase 28: microstructure analysis from enriched trade rows.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import MOMENTUM_V2_REFERENCE

MICRO_HORIZONS = (15, 30, 60, 90)


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _dist(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "p50": None, "mean": None}
    s = sorted(vals)
    return {"count": len(s), "p50": statistics.median(s), "mean": statistics.mean(s)}


def _rate(grp: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    if not grp:
        return None
    return sum(1 for r in grp if r.get(key)) / len(grp)


def _horizon_compare(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Any]:
    winners = [r for r in rows if float(r["pnl_pct"]) > 0]
    losers = [r for r in rows if float(r["pnl_pct"]) < 0]

    def _field(grp: Sequence[Mapping[str, Any]], key: str) -> list[float]:
        out: list[float] = []
        for r in grp:
            v = _as_float(r.get(key))
            if v is not None:
                out.append(v)
        return out

    horizons: dict[str, Any] = {}
    for h in MICRO_HORIZONS:
        horizons[f"{h}s"] = {
            "winners_spread_ratio": _dist(_field(winners, f"early_{h}s_minute_tv_change_ratio")),
            "losers_spread_ratio": _dist(_field(losers, "spread_expansion_ratio")),
            "winners_imb_change": _dist(_field(winners, f"early_{h}s_board_imbalance_change")),
            "losers_imb_change": _dist(_field(losers, f"early_{h}s_board_imbalance_change")),
            "winners_momentum": _dist(_field(winners, f"early_{h}s_momentum_pct_from_entry")),
            "losers_momentum": _dist(_field(losers, f"early_{h}s_momentum_pct_from_entry")),
            "winners_vwap_change": _dist(_field(winners, f"early_{h}s_vwap_distance_change")),
            "losers_vwap_change": _dist(_field(losers, f"early_{h}s_vwap_distance_change")),
        }

    return {
        "label": label,
        "trade_count": len(rows),
        "winners": len(winners),
        "losers": len(losers),
        "horizons": horizons,
    }


def build_microstructure_analysis(trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(trade_rows)
    n = len(rows)

    fake_rate = _rate(rows, "breakout_at_entry")
    fake_high = sum(1 for r in rows if (_as_float(r.get("fake_breakout_score")) or 0) >= 0.55) / n if n else None
    recovery_success = sum(
        1 for r in rows if r.get("recovered_after_adverse") and float(r.get("pnl_pct", 0)) > 0
    )
    recovered_n = sum(1 for r in rows if r.get("recovered_after_adverse"))
    vwap_reclaim = _rate(rows, "vwap_reclaim_achieved")
    noise_rev = _rate(rows, "noise_reversal")

    spread_exp = [_as_float(r.get("spread_expansion_ratio")) for r in rows]
    spread_exp = [x for x in spread_exp if x is not None]

    notes = [
        "yahoo_replay_vs_live: spread/imbalance noise dominates exits",
        "breakout_follow_underperforms_when_fake_breakout_score_high",
        "v6_goal: tolerate_noise_cut_only_structure_break",
    ]
    if fake_high and fake_high > 0.4:
        notes.append("high_fake_breakout_share_suggests_breakout_chase_weak_in_microstructure")
    if recovered_n and recovery_success / recovered_n > 0.15:
        notes.append("recovery_trades_exist_hold_not_immediate_cut")

    return {
        "phase": 28,
        "reference_profile": MOMENTUM_V2_REFERENCE,
        "trade_count": n,
        "spread_expansion": _dist(spread_exp),
        "spread_expansion_rate_severe": (
            sum(1 for x in spread_exp if x >= 1.4) / len(spread_exp) if spread_exp else None
        ),
        "imbalance_collapse_persistence": _dist(
            [_as_float(r.get("imbalance_collapse_max_streak")) for r in rows if r.get("imbalance_collapse_max_streak") is not None]
        ),
        "adverse_persistence": _dist(
            [_as_float(r.get("adverse_persistence_count")) for r in rows if r.get("adverse_persistence_count") is not None]
        ),
        "favorable_persistence": _dist(
            [_as_float(r.get("favorable_persistence_count")) for r in rows if r.get("favorable_persistence_count") is not None]
        ),
        "vwap_reclaim_success_rate": vwap_reclaim,
        "fake_breakout_rate": fake_high,
        "fake_breakout_score": _dist([_as_float(r.get("fake_breakout_score")) or 0 for r in rows]),
        "recovery_success_rate": (recovery_success / recovered_n) if recovered_n else None,
        "noise_reversal_rate": noise_rev,
        "adverse_persistence_rate": _rate(
            [r for r in rows if (_as_float(r.get("adverse_persistence_count")) or 0) >= 3], "adverse_persistence_count"
        ),
        "favorable_persistence_rate": _rate(
            [r for r in rows if (_as_float(r.get("favorable_persistence_count")) or 0) >= 2],
            "favorable_persistence_count",
        ),
        "overall": _horizon_compare(rows, "all"),
        "winners_only": _horizon_compare([r for r in rows if float(r["pnl_pct"]) > 0], "winners"),
        "losers_only": _horizon_compare([r for r in rows if float(r["pnl_pct"]) < 0], "losers"),
        "yahoo_vs_live_adaptation": {
            "problem": "breakout_visible_on_yahoo_collapses_on_board_microstructure",
            "direction": "noise_tolerant_exit_and_structure_break_only",
        },
        "diagnosis_notes": notes,
    }

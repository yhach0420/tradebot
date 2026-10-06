"""Predeclared structural detector rules. Fixed before any V2 PnL join. Not tuned on PF."""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# PREDECLARED STRUCTURAL RULES (document before looking at V2 economics)
# ---------------------------------------------------------------------------
# Design intent:
# - Recognize a local high ONLY after observable downward rejection (not lookback max).
# - Cluster rejected highs from the observed price span (not fixed ±1 tick alone).
# - Independent re-test requires material leave-and-return.
# - Distinguish upper rejection from two-way consolidation descriptively.
# - Breakout/retest states record anatomy; acceptance markers are descriptive only.
# - Forbidden as inputs / selection criteria: future PnL, MFE, MAE, ratchet, exit,
#   hold, win/loss.
# ---------------------------------------------------------------------------

DETECTOR_RULES: dict[str, Any] = {
    "version": "STRUCTURAL_RESISTANCE_DETECTOR_RULES_V1",
    "tick_source": "research.low_price_risk_review.jpx_tick_size_yen",
    "starts_from_pre_break_high": False,
    # Swing recognition (ticks via jpx_tick_size_yen; not lookback-max)
    "min_leg_ticks": 1,  # establish a directional leg
    "rejection_confirm_ticks": 2,  # observable leave from pending extreme to recognize turn
    "stall_events_hint": 2,  # descriptive stall counter (not a score)
    # Independent re-test
    "material_away_ticks": 3,  # must leave below zone_low by this many ticks
    # Zone clustering from observed rejected highs
    "cluster_join_ticks": 4,  # join a new swing high into a zone if within this of zone span
    "zone_pad_ticks": 0,  # zone span = observed min/max of member highs (no forced ±1)
    # Classification (descriptive counts; not PnL-tuned)
    "upper_rejection_min_independent_tests": 2,
    "upper_rejection_min_rejections": 2,
    "consolidation_min_through_each_way": 2,
    # Breakout anatomy markers (not optimized acceptance thresholds)
    "break_candidate_min_events_above": 3,
    "break_candidate_min_progress_ticks": 2,
    "support_response_away_ticks": 2,  # after retest, move up by this many ticks
}


def detector_document() -> dict[str, Any]:
    """Frozen identity body for the detector implementation."""
    return {
        "ANALYSIS_ID": "V2_STRUCTURAL_RESISTANCE_DETECTOR_VALIDATION_V1",
        "rules": DETECTOR_RULES,
        "zone_types": [
            "UPPER_REJECTION_ZONE",
            "CONSOLIDATION_ZONE",
            "SINGLE_SWING_HIGH",
            "UNRESOLVED_ZONE",
        ],
        "zone_states": [
            "RESISTANCE_ACTIVE",
            "BREAK_ATTEMPT",
            "BREAK_ACCEPTED_CANDIDATE",
            "RETEST",
            "SUPPORT_CONFIRMED",
            "FAILED_BREAKOUT",
            "INVALIDATED",
        ],
        "retest_labels": [
            "SUPPORT_RESPONSE",
            "FAILED_SUPPORT",
            "NO_RETEST",
            "UNRESOLVED",
        ],
        "next_resistance_scope": "all_causally_known_same_session_structure",
        "forbidden_inputs": [
            "future_pnl",
            "future_mfe",
            "future_mae",
            "future_ratchet_count",
            "future_exit_reason",
            "future_hold_time",
            "win_loss",
        ],
    }

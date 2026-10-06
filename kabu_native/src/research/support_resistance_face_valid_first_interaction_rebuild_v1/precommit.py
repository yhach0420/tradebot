"""Predeclared later matched-test and outcome metrics. Not run in this phase. No PnL selection."""
from __future__ import annotations

from typing import Any


def matched_test_precommit() -> dict[str, Any]:
    return {
        "name": "SUPPORT_RESISTANCE_FIRST_INTERACTION_MATCHED_CAUSAL_TEST_V1",
        "run_in_this_phase": False,
        "treatment": "first causal interaction episode with a selected salient zone",
        "matching_dimensions": [
            "same symbol",
            "similar time of day",
            "similar 1m momentum",
            "similar 3m momentum",
            "similar 5m momentum",
            "similar realized volatility",
            "similar relative volume",
            "similar gap state",
            "similar market-relative state",
            "similar sector-relative state",
        ],
        "no_future_outcome_for_matching": True,
        "placebo_levels": [
            "prior-day midpoint, same 0.15 ATR half-width, discarded if overlapping a real selected zone",
            "prior close + 0.5 ATR, same width, discarded if overlap",
        ],
        "separate_questions": {
            "A": "FIRST TOUCH → REJECTION",
            "B": "BREAK → CONTINUATION",
            "C": "BREAK → RETEST → HOLD",
            "D": "FAILED BREAK",
        },
        "do_not_use_5pp_continuation_as_sole_criterion": True,
    }


def outcome_precommit() -> dict[str, Any]:
    return {
        "run_in_this_phase": False,
        "not_sole_criterion": "5pp continuation-rate gap",
        "metrics": [
            "full return distribution",
            "MFE",
            "MAE",
            "MFE-before-MAE",
            "+20bps before -20bps",
            "+40bps before -20bps",
            "+80bps before -30bps",
            "failure probability",
            "time to failure",
            "time to extension",
            "payoff asymmetry",
        ],
        "uncertainty": "symbol/day-aware resampling",
        "no_fixed_horizon_pnl_optimization": True,
        "x0_x1_not_used_to_select_rules": True,
    }

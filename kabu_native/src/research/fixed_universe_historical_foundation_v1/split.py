"""Chronological research split. Dates unassigned until common period is known."""
from __future__ import annotations

from typing import Any

from research.fixed_universe_historical_foundation_v1 import (
    HISTORICAL_MIN_YEARS,
    HISTORICAL_PREFERRED_YEARS,
    RANDOM_SPLIT_PRIMARY,
    TEST_SET_REUSE_AFTER_PARAM_CHANGE,
)

DISCOVERY_FRAC = 0.60
CONFIRMATION_FRAC = 0.20
FROZEN_VALIDATION_FRAC = 0.20


def split_plan() -> dict[str, Any]:
    return {
        "primary": "chronological",
        "random_split_primary": bool(RANDOM_SPLIT_PRIMARY),
        "discovery_frac": float(DISCOVERY_FRAC),
        "confirmation_frac": float(CONFIRMATION_FRAC),
        "frozen_validation_frac": float(FROZEN_VALIDATION_FRAC),
        "dates_assigned": False,
        "reason_dates_unassigned": "common_overlap_period_unknown_until_sources_are_ingested",
        "min_years": int(HISTORICAL_MIN_YEARS),
        "preferred_years": int(HISTORICAL_PREFERRED_YEARS),
        "equity_minute_cap_years": 2,
        "common_period_rule": (
            "intersection of valid JST sessions across stocks, JP futures, USDJPY, "
            "and any joined external series after availability-time alignment"
        ),
        "test_set_reuse_after_param_change": bool(TEST_SET_REUSE_AFTER_PARAM_CHANGE),
        "if_frozen_validation_viewed_then_params_changed": (
            "that_validation_window_is_burned_and_cannot_be_the_next_version_validation"
        ),
        "walk_forward_optional_later": "not_primary_in_phase_0",
        "no_pnl_to_choose_split": True,
        "plan_ready": True,
    }


def rows() -> list[dict[str, Any]]:
    p = split_plan()
    return [{"key": k, "value": v} for k, v in p.items()]


assert abs(DISCOVERY_FRAC + CONFIRMATION_FRAC + FROZEN_VALIDATION_FRAC - 1.0) < 1e-12
assert RANDOM_SPLIT_PRIMARY is False
assert TEST_SET_REUSE_AFTER_PARAM_CHANGE is False
assert split_plan()["dates_assigned"] is False
assert split_plan()["plan_ready"] is True

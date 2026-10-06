"""V1.1 contract: unchanged hypothesis, corrected calendar/shuffle/folds."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.phase2_precommit.contract import frozen_contract as frozen_contract_v1
from research.causal_driver_pb1.phase2_precommit import (
    BOOTSTRAP_METHOD,
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    DAY_ELIGIBILITY_EXPECTED_N,
    DAY_ELIGIBILITY_MIN_COVERAGE,
    DAY_ELIGIBILITY_WINDOW,
    DAY_SHUFFLE_N,
    DAY_SHUFFLE_SEED,
    MKT105_MIN_VALID_SYMBOLS,
    SECTOR_CLOCK_COVERAGE_MIN,
)
from research.causal_driver_pb1.phase2_precommit_v1_1 import (
    DAY_SHUFFLE_METHOD,
    PRECOMMIT_ID,
    SUPERSEDED_FOLD_BOUNDARY_SHA256,
    SUPERSEDES_PRECOMMIT_SHA256,
    TSE_AM_MIN_SYMBOLS,
    TSE_AM_WINDOW,
)


def frozen_contract_v1_1(
    *,
    stock: dict[str, Any],
    sectors: dict[str, Any],
    folds: dict[str, Any],
    phase1: dict[str, Any],
    calendar: dict[str, Any],
    shuffle: dict[str, Any],
) -> dict[str, Any]:
    base = frozen_contract_v1(stock=stock, sectors=sectors, folds=folds, phase1=phase1)
    base.pop("precommit_sha256", None)
    base["precommit_id"] = PRECOMMIT_ID
    base["supersedes_precommit_sha256"] = SUPERSEDES_PRECOMMIT_SHA256
    base["superseded_fold_boundary_sha256"] = SUPERSEDED_FOLD_BOUNDARY_SHA256
    base["day_eligibility"] = {
        "definition": "PHASE2_ELIGIBLE_DAY = allowed_period AND TSE_CASH_TRADING_DAY AND USDJPY_VALID_PAIRED_COVERAGE_0830_1130 >= 0.95",
        "tse_calendar_method": calendar.get("method"),
        "tse_calendar_source_identity": calendar.get("source_identity"),
        "tse_calendar_source_sha256": calendar.get("source_sha256"),
        "am_presence_window": f"{TSE_AM_WINDOW[0]}-{TSE_AM_WINDOW[1]}",
        "am_presence_min_symbols": TSE_AM_MIN_SYMBOLS,
        "fx_window_jst": f"{DAY_ELIGIBILITY_WINDOW[0]}-{DAY_ELIGIBILITY_WINDOW[1]}",
        "fx_expected_n": DAY_ELIGIBILITY_EXPECTED_N,
        "fx_min_ratio": DAY_ELIGIBILITY_MIN_COVERAGE,
        "named_date_exclusions_forbidden": True,
        "whole_day_vs_clock": {
            "day_eligibility_used_for": ["development_dates", "c1_dates", "folds", "date_block_bootstrap", "leave_one_month_out"],
            "per_clock_stock_eligibility_unchanged": {
                "mkt105_min_valid_symbols": MKT105_MIN_VALID_SYMBOLS,
                "sector_clock_coverage_min": SECTOR_CLOCK_COVERAGE_MIN,
            },
            "per_clock_missingness_does_not_redefine_folds": True,
        },
        "hardcoded_holiday_names": False,
        "outcome_used": False,
    }
    base["day_shuffle_gate"] = {
        "n": DAY_SHUFFLE_N,
        "seed": DAY_SHUFFLE_SEED,
        "method": DAY_SHUFFLE_METHOD,
        "primary_stratum": "(YYYY-MM, weekday)",
        "fallback_stratum": "YYYY-MM among leftover dates not already in a primary group of size>=2",
        "unshufflable": "month leftover size<2: exclude from shuffle placebo only, keep in primary analysis",
        "no_self_mapping": True,
        "identity_mapping_not_a_placebo": True,
        "requirement": "real |statistic| >= empirical 95th percentile of |shuffled statistic|",
        "shuffle_primary_n": shuffle.get("shuffle_primary_n"),
        "shuffle_fallback_month_n": shuffle.get("shuffle_fallback_month_n"),
        "shuffle_unshufflable_n": shuffle.get("shuffle_unshufflable_n"),
        "permutation_sha256": shuffle.get("permutation_sha256"),
        "reproducible": True,
    }
    base["bootstrap_method"] = BOOTSTRAP_METHOD
    base["bootstrap_n"] = BOOTSTRAP_N
    base["bootstrap_seed"] = BOOTSTRAP_SEED
    base["bootstrap_eligible_day_population"] = "PHASE2_ELIGIBLE_DAY"
    base["lomo_eligible_dates"] = "PHASE2_ELIGIBLE_DAY"
    base["PHASE2_OUTCOMES_OPENED"] = False
    base["candidate_list_sha256"] = None
    base["precommit_sha256"] = sha256_obj(base)
    return base

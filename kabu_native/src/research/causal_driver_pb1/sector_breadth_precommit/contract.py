"""Frozen sector breadth/dispersion research contract. No outcomes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit import (
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    CLOCK_FIRST,
    CLOCK_LAST,
    DAY_SHUFFLE_METHOD,
    DAY_SHUFFLE_N,
    DAY_SHUFFLE_SEED,
    DEPENDENCY_WINDOW,
    DRIVER_AGE_SEC,
    EXPECTED_SECTOR_IDS,
    FAMILY_N,
    FDR_FAMILY_N,
    FDR_Q,
    GLOBAL_MIN_FRAC,
    GLOBAL_MIN_VALID,
    HORIZON_LAST_T,
    HORIZONS,
    LOOKBACKS,
    METRICS,
    MKT_EX_MIN_FRAC,
    MKT_EX_MIN_N,
    OFFSETS,
    PAST_CONTROL_MIN,
    PEER_MIN_FRAC,
    PEER_MIN_N,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PRECOMMIT_ID,
    PRIMARY_NEXT_DRIVER,
    RESEARCH_FAMILY_ID,
    SCOPE_N,
    STRICT_TARGET_AGE_SEC,
    TARGET_AGE_SEC,
)
from research.causal_driver_pb1.sector_breadth_precommit.family import family_identity


def frozen_contract(*, stock: dict[str, Any], sectors: dict[str, Any], days: dict[str, Any]) -> dict[str, Any]:
    eligible = list(sectors.get("eligible_sectors") or [])
    family = family_identity(eligible_sectors=eligible)
    scopes = family["scopes"]
    folds = days.get("folds") or {}
    shuffle = days.get("shuffle") or {}
    contract = {
        "precommit_id": PRECOMMIT_ID,
        "RESEARCH_FAMILY_ID": RESEARCH_FAMILY_ID,
        "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
        "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        "phase0_observation_family_if_later_bound": "JP_INTERNAL",
        "research_question": (
            "Does cross-sectional participation or dispersion inside a sector at or before T "
            "contain incremental information about the sector's subsequent equal-weight return, "
            "after controlling for the sector's own recent mean return and rest-of-market recent return?"
        ),
        "interpretation": "cross-sectional internal state to subsequent aggregate movement; not individual stock causes another stock",
        "not": ("PB1", "target-self technical", "single leader-laggard", "old peer/SR playbook"),
        "eligible_periods": {
            "DEVELOPMENT": {"first": DEV_FIRST, "last": DEV_LAST, "role": "candidate_discovery"},
            "ECONOMIC_DEVELOPMENT_EXPOSED": {"first": C1_FIRST, "last": C1_LAST, "role": "locked_oot_confirmation"},
            "FROZEN_VALIDATION": {"first": FV_FIRST, "last": FV_LAST, "access": "DENY"},
            "PROSPECTIVE": {"first": PROSPECTIVE_FROM, "access": "DENY"},
        },
        "research_universe": {
            "n": 105,
            "sha256": sectors.get("universe105_sha256"),
            "dynamic40": False,
            "pb1_candidates": False,
        },
        "sector_mapping": {
            "sha256": sectors.get("sector_mapping_sha256"),
            "eligible_rule": "point_in_time_frozen_pool_sector_constituent_n_ge_3",
            "eligible_sector_ids": list(EXPECTED_SECTOR_IDS),
            "eligible_sector_n": 11,
        },
        "scopes": scopes,
        "metrics": {
            "n": 2,
            "ids": list(METRICS),
            "SECTOR_BREADTH": {
                "RET_i_w": "10000*log(P_i(T)/P_i(T-w))",
                "BREADTH_w": "(N_up - N_down) / N_valid",
                "N_up": "return > 0",
                "N_down": "return < 0",
                "zero_return": "contributes 0 to numerator; included in N_valid",
                "range": [-1, 1],
            },
            "DISPERSION": {
                "DISPERSION_w": "cross-sectional sample standard deviation of RET_i,w(T)",
                "units": "bps",
                "no_winsor_search": True,
                "no_MAD_search": True,
                "no_vol_normalization_search": True,
            },
        },
        "family": {
            "metric_n": 2,
            "scope_n": SCOPE_N,
            "lookbacks": list(LOOKBACKS),
            "horizons": list(HORIZONS),
            "n": FAMILY_N,
            "family_384_sha256": family.get("family_384_sha256"),
            "no_interaction_search": True,
            "no_threshold_search": True,
            "no_individual_stock_winners": True,
        },
        "family_384": family.get("tests"),
        "resolver": {
            "timestamp_semantics": "BAR_START",
            "available_at": "bar_start + 1m",
            "price": "last_completed_close_available_at_le_T",
            "same_session_only": True,
            "prior_day_carry": False,
            "exact_minute_print_required": False,
            "fabricated_mid": False,
            "interpolation": False,
            "future_fill": False,
            "zero_fill": False,
            "stock_probe": {k: stock.get(k) for k in stock if k != "parquet_schema_names"},
        },
        "freshness": {
            "driver_price_age_sec": DRIVER_AGE_SEC,
            "driver_both_endpoints": True,
            "target_price_age_sec": TARGET_AGE_SEC,
            "strict_target_price_age_sec": STRICT_TARGET_AGE_SEC,
            "strict_cannot_rescue_primary_failure": True,
        },
        "research_clock": {
            "first": CLOCK_FIRST,
            "last": CLOCK_LAST,
            "grid": "native_1m",
            "timezone": "Asia/Tokyo",
            "horizon_last_T": dict(HORIZON_LAST_T),
            "do_not_cross_1130": True,
        },
        "day_eligibility": {
            "ELIGIBLE_DAY": "official TSE cash trading day AND >=95% of 09:00-11:30 minutes have GLOBAL valid n>=90 AND valid fraction of PIT listed 105 >=80% with age<=60s",
            "DEPENDENCY_WINDOW": f"{DEPENDENCY_WINDOW[0]}-{DEPENDENCY_WINDOW[1]} JST inclusive",
            "input_only": True,
            "usdjpy_fx_exclusions_inherited": False,
            "leader_laggard_days_inherited": False,
        },
        "point_in_time_listing": {
            "before_listing": "NOT_LISTED",
            "no_replacement": True,
            "no_backward_carry_of_future_listed": True,
        },
        "scope_clock_eligibility": {
            "sector_driver": "valid n >= max(3, ceil(0.80 * PIT listed sector n)) at T and T-w; same valid-name intersection",
            "global_driver": f"valid n >= {GLOBAL_MIN_VALID} AND valid fraction of PIT listed universe >= {GLOBAL_MIN_FRAC} at both endpoints",
            "missing_not_zero": True,
        },
        "target_response": {
            "class": "INFORMATION_DISCOVERY_OUTCOME",
            "sector": f"EQW valid constituent future returns; valid n>={PEER_MIN_N} AND frac>={PEER_MIN_FRAC}",
            "global": f"EQW valid PIT listed 105; valid n>={GLOBAL_MIN_VALID} AND frac>={GLOBAL_MIN_FRAC}",
            "not_fills": True,
            "not_tradable_pnl": True,
        },
        "controls": {
            "sector_past_5m": "EQW constituent return T-5m to T",
            "mkt_ex_sector_past_5m": f"EQW 105 excluding target sector; valid n>={MKT_EX_MIN_N} AND frac>={MKT_EX_MIN_FRAC}",
            "global_past_5m_only_for_global_scope": True,
            "past_control_min": PAST_CONTROL_MIN,
        },
        "models": {
            "MODEL0": "controls only",
            "MODEL1": "MODEL0 + breadth/dispersion driver",
            "sector": (
                "SECTOR_FUTURE_RETURN = b_driver * DRIVER_w + b_sector * SECTOR_PAST_5M "
                "+ b_market * MKT_EX_SECTOR_PAST_5M + minute_of_day_FE + error"
            ),
            "global": "GLOBAL_FUTURE_RETURN = b_driver * GLOBAL_DRIVER_w + b_market * GLOBAL_PAST_5M + minute_of_day_FE + error",
            "minute_of_day_FE": "within_minute_demeaning",
            "primary_parameter": "beta_driver",
            "no_new_controls": True,
        },
        "inference": {
            "bootstrap_method": "DATE_BLOCK_BOOTSTRAP",
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "sampling_unit": "eligible trading date",
            "fdr_method": "BENJAMINI_HOCHBERG",
            "fdr_q": FDR_Q,
            "fdr_family_n": FDR_FAMILY_N,
            "quintiles": "DEV-only metric x scope x lookback freeze Q20/Q40/Q60/Q80 before C1",
        },
        "dev_gates": {
            "D1": "DEV_EARLY and DEV_LATE b_driver same sign",
            "D2": "pooled DEV date-block bootstrap 95% CI excludes 0",
            "D3": "BH FDR q <= 0.05 on family n=384",
            "D4": "Q5-Q1 sign agrees with b_driver",
            "D5": "minute mod-5 >= 4/5 subgrids same sign",
            "D6": "LOMO >= 75% same sign",
            "D7": {
                "name": "STRICT_TARGET_MAX_AGE_60",
                "cannot_rescue_D1_D6_failure": True,
                "pass_requires_all": (
                    "sign(beta_60) = sign(beta_primary)",
                    "date-block bootstrap 95% CI for beta_60 excludes 0",
                    "abs(beta_60) >= 0.50 * abs(beta_primary)",
                ),
            },
            "all_required": True,
            "zero_candidates": "SECTOR_BREADTH_DISPERSION_CAUSAL_LEAD_NOT_FOUND_V1; do not open C1; NEXT REASSESS_NEW_CAUSAL_INFORMATION_ACQUISITION_V2",
        },
        "c1_gates": {
            "opened_only_if_frozen_dev_candidate_n_ge_1": True,
            "C1": "pooled b same sign as DEV",
            "C2": "C1 date-block bootstrap 95% CI excludes 0",
            "C3": ">=2/3 chronological C1 folds same sign",
            "C4": "opposite fold with |b| > 1.5x DEV pooled FAIL",
            "C5": "DEV-frozen Q5-Q1 direction confirmed",
            "C6": "LOMO >= 75% same sign",
            "C7": {
                "cannot_rescue_C1_C6_failure": True,
                "pass_requires_all": (
                    "sign(beta_C1_60) = sign(beta_DEV)",
                    "C1 date-block bootstrap 95% CI for beta_C1_60 excludes 0",
                    "abs(beta_C1_60) >= 0.50 * abs(beta_C1_primary)",
                ),
            },
        },
        "placebos": {
            "time_offset": {
                "offsets_min": list(OFFSETS),
                "outcome_fixed_T_to_T_plus_h": True,
                "controls_fixed_at_T": True,
                "shift_driver_only": True,
                "positive_offsets": "NON_CAUSAL_FUTURE_PLACEBO",
                "require_0": True,
                "require_one_of_m1_m3": True,
                "future_only_shape_absent": True,
                "abs_0_ge_0_50_times_max_future_1_3_5": True,
                "fail_label": "FOLLOWER_OR_CONTEMPORANEOUS_NOT_LEAD",
            },
            "day_shuffle": {
                "n": DAY_SHUFFLE_N,
                "seed": DAY_SHUFFLE_SEED,
                "method": DAY_SHUFFLE_METHOD,
                "primary": "(YYYY-MM, weekday)",
                "fallback": "YYYY-MM",
                "no_self_mapping": True,
                "shuffle_driver_day_only": True,
                "real_abs_ge_empirical_p95": True,
                "permutation_sha256": shuffle.get("permutation_sha256"),
            },
            "sector_identity_specificity_gate": {
                "label": "SECTOR_IDENTITY_SPECIFICITY_GATE",
                "not_empirical_p95": True,
                "not_p_lt_0_05": True,
                "same_sector": {
                    "hold_fixed": ("target_sector", "metric", "lookback", "horizon", "dates", "clock", "controls", "freshness"),
                    "alternative_sectors_n": 10,
                    "pass_if": "abs(beta_actual) > max(abs(beta_alt_1), ..., abs(beta_alt_10))",
                    "ties_fail": True,
                },
                "GLOBAL_105": "NOT_APPLICABLE",
            },
        },
        "concentration": {
            "sector_driver": "leave-one-constituent-out of driver >=80% same sign AND drop most-influential constituent sign remains",
            "sector_target": "leave-one-target-out >=80% same sign AND drop largest absolute contributor sign remains",
            "global": "drop top 1 and top 5 driver-influence constituents AND drop top 1 and top 5 target contributors; all same sign",
        },
        "common_factor_check": {
            "sector_only": "remove MKT_EX_SECTOR_PAST_5M; beta_driver sign unchanged; cannot rescue primary failure",
        },
        "no_pb1": True,
        "no_complete_strategy": True,
        "pass_verdict": "SECTOR_BREADTH_DISPERSION_CAUSAL_LEAD_FOUND_V1",
        "pass_next": "PRECOMMIT_SECTOR_STATE_SYMBOL_TRANSMISSION_V1",
        "fail_verdict": "SECTOR_BREADTH_DISPERSION_CAUSAL_LEAD_NOT_FOUND_V1",
        "fail_next": "REASSESS_NEW_CAUSAL_INFORMATION_ACQUISITION_V2",
        "folds": {
            "split_rule": folds.get("split_rule"),
            "development_fold_boundaries": folds.get("development_fold_boundaries"),
            "c1_fold_boundaries": folds.get("c1_fold_boundaries"),
            "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
            "eligible_day_sha256": days.get("eligible_day_sha256"),
        },
        "c1_access_barrier": {
            "STAGE_A_hard_stop": "20251126",
            "C1_rows_read_before_candidate_freeze": 0,
            "candidate_list_sha256_required_before_C1": True,
        },
        "SECTOR_BREADTH_DISPERSION_OUTCOMES_OPENED": False,
        "C1_outcomes_opened": False,
        "candidate_list_sha256": None,
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
    }
    contract["precommit_sha256"] = sha256_obj(contract)
    return contract

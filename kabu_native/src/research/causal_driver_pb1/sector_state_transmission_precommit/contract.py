"""Frozen transmission contract. Gate text only. No betas."""
from __future__ import annotations

from math import ceil
from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_transmission_precommit import (
    BH_FAMILY_M,
    COVERAGE_FLOOR_N,
    COVERAGE_FRACTION,
    F5_ABS_RATIO,
    FAMILY_N,
    FDR_Q,
    GLOBAL_MAX_SECTOR_SHARE,
    GLOBAL_MIN_DISTINCT_SECTORS,
    GLOBAL_TARGET_N,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
    SECTOR3650_TARGET_N,
    T6_ABS_RATIO,
)


def frozen_contract(**parts: Any) -> dict[str, Any]:
    global_min = max(int(COVERAGE_FLOOR_N), int(ceil(float(COVERAGE_FRACTION) * int(GLOBAL_TARGET_N))))
    sector_min = max(int(COVERAGE_FLOOR_N), int(ceil(float(COVERAGE_FRACTION) * int(SECTOR3650_TARGET_N))))
    base: dict[str, Any] = {
        "precommit_id": PRECOMMIT_ID,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "parent_mechanism_set_sha256": parts["parent_mechanism_set_sha256"],
        "global_target_set_sha256": parts["global_target_set_sha256"],
        "sector3650_target_set_sha256": parts["sector3650_target_set_sha256"],
        "family_n": int(FAMILY_N),
        "family_sha256": parts["family_sha256"],
        "discovery_date_sha256": parts["discovery_date_sha256"],
        "fv_eligible_day_sha256": parts["fv_eligible_day_sha256"],
        "fv_fold_sha256": parts["fv_fold_sha256"],
        "transmission_discovery_bootstrap_index_sha256": parts["discovery_bootstrap_sha256"],
        "fv_bootstrap_index_sha256": parts["fv_bootstrap_sha256"],
        "direction": "UP",
        "beta_driver_gt_0": True,
        "negative_coefficient_is_fail_not_a_short": True,
        "leave_target_out": True,
        "models": {
            "global": "Y_s = b_driver*GLOBAL_BREADTH_EX_TARGET_s + b_self*TARGET_PAST_5M_s + b_sector*SECTOR_EX_TARGET_PAST_5M_s + b_market*MKT_EX_SECTOR_PAST_5M + minute_FE",
            "sector3650": "Y_s = b_driver*SECTOR3650_BREADTH_EX_TARGET_s + b_self*TARGET_PAST_5M_s + b_sector*SECTOR3650_EX_TARGET_PAST_5M_s + b_market*MKT_EX_3650_PAST_5M + minute_FE",
            "primary_parameter": "beta_driver",
            "no_new_covariates": True,
        },
        "inference": {
            "ci": "BOOTSTRAP_PERCENTILE_CI",
            "ci_quantiles": [2.5, 97.5],
            "quantile_interpolation": "linear",
            "p_value": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
            "no_iid_ols_p": True,
            "discovery_bh": {"method": "BENJAMINI_HOCHBERG_MONOTONE_Q", "m": int(BH_FAMILY_M), "q": float(FDR_Q), "not_split_by_parent_sector_horizon_target": True},
            "fv_bh": {"method": "BENJAMINI_HOCHBERG_MONOTONE_Q", "m": "frozen transmission candidate n", "q": float(FDR_Q)},
        },
        "discovery_gates": {
            "T1": "beta_DISCOVERY_DEV>0 AND beta_DISCOVERY_C1>0",
            "T2": "pooled discovery bootstrap 95% CI lower>0",
            "T3": "BH q<=0.05 on m=270",
            "T4": "minute modulo 5, >=4/5 subgroup betas>0",
            "T5": "LOMO >=75% beta>0",
            "T6": f"beta_60>0 AND CI_60 lower>0 AND beta_60>= {T6_ABS_RATIO}*beta_primary; cannot rescue T1-T5",
        },
        "fv_gates": {
            "F1": "beta_FV>0",
            "F2": "FV bootstrap 95% CI lower>0",
            "F3": ">=2/3 chronological FV folds beta>0",
            "F4": "FV LOMO >=75% beta>0",
            "F5": f"beta_FV_60>0 AND CI_FV_60 lower>0 AND beta_FV_60>={F5_ABS_RATIO}*beta_FV_primary",
            "F6": "BH q<=0.05 across frozen candidate n",
            "scan_all_270_again": False,
        },
        "coverage_gates": {
            "rule": "confirmed_target_n >= max(5, ceil(0.10 * parent_target_universe_n))",
            "GLOBAL": global_min,
            "SECTOR_3650": sector_min,
            "global_distinct_sectors_ge": int(GLOBAL_MIN_DISTINCT_SECTORS),
            "global_max_sector_share": float(GLOBAL_MAX_SECTOR_SHARE),
        },
        "zero_candidate_rule": {
            "if_transmission_candidate_n_eq_0": "do not open FV",
            "VERDICT": "SECTOR_STATE_SYMBOL_TRANSMISSION_NOT_FOUND_V1",
            "NEXT": "REASSESS_ALPHA_ARCHITECTURE_AFTER_AGGREGATE_ONLY_DRIVER_V1",
        },
        "family_pass": {"VERDICT": "SECTOR_STATE_SYMBOL_TRANSMISSION_FOUND_V1", "NEXT": "PRECOMMIT_SECTOR_STATE_ALPHA_SIGNAL_V1", "ALPHA_CREATED": False},
        "family_fail": {"VERDICT": "SECTOR_STATE_SYMBOL_TRANSMISSION_NOT_FOUND_V1", "NEXT": "REASSESS_ALPHA_ARCHITECTURE_AFTER_AGGREGATE_ONLY_DRIVER_V1"},
        "no_best_symbol_ranking": True,
        "no_alpha_threshold_search": True,
        "no_execution": True,
        "symbol_outcomes_opened_in_this_precommit": False,
    }
    base["precommit_sha256"] = sha256_obj(base)
    return base

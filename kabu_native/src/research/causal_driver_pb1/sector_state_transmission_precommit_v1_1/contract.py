"""Frozen V1.1 transmission contract. Gate text only. No betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import (
    CONTROL_CONTRACT_ID,
    DISCOVERY_BOOTSTRAP_SHA256,
    DISCOVERY_DATE_SHA256,
    DISCOVERY_FOLD_SHA256,
    FAMILY_SERIALIZATION_SHA256,
    FV_BOOTSTRAP_SHA256,
    FV_ELIGIBLE_DAY_SHA256,
    FV_FOLD_SHA256,
    GLOBAL_TARGET_SET_SHA256,
    PARENT_MECHANISM_SET_SHA256,
    PARENT_PRECOMMIT_SHA256,
    PRECOMMIT_ID,
    SECTOR3650_TARGET_SET_SHA256,
)


def frozen_contract(**parts: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "precommit_id": PRECOMMIT_ID,
        "symbol_control_contract_id": CONTROL_CONTRACT_ID,
        "parent_precommit_sha256": PARENT_PRECOMMIT_SHA256,
        "parent_mechanism_set_sha256": PARENT_MECHANISM_SET_SHA256,
        "global_target_set_sha256": GLOBAL_TARGET_SET_SHA256,
        "sector3650_target_set_sha256": SECTOR3650_TARGET_SET_SHA256,
        "family_serialization_sha256": FAMILY_SERIALIZATION_SHA256,
        "family_hypothesis_sha256": parts["family_hypothesis_sha256"],
        "model_contract_sha256": parts["model_contract_sha256"],
        "symbol_control_contract_sha256": parts["symbol_control_contract_sha256"],
        "discovery_date_sha256": DISCOVERY_DATE_SHA256,
        "discovery_fold_sha256": DISCOVERY_FOLD_SHA256,
        "fv_eligible_day_sha256": FV_ELIGIBLE_DAY_SHA256,
        "fv_fold_sha256": FV_FOLD_SHA256,
        "transmission_discovery_bootstrap_index_sha256": DISCOVERY_BOOTSTRAP_SHA256,
        "fv_bootstrap_index_sha256": FV_BOOTSTRAP_SHA256,
        "family_n": 270,
        "bh_discovery_m": 270,
        "bh_q": 0.05,
        "inference": {
            "ci": "BOOTSTRAP_PERCENTILE_CI",
            "ci_quantiles": [2.5, 97.5],
            "quantile_interpolation": "linear",
            "p_value": "BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE",
            "discovery_bh": {"method": "BENJAMINI_HOCHBERG_MONOTONE_Q", "m": 270, "q": 0.05},
            "fv_bh": {"method": "BENJAMINI_HOCHBERG_MONOTONE_Q", "m": "frozen transmission candidate n", "q": 0.05},
        },
        "discovery_gates": {
            "T1": "beta_DISCOVERY_DEV>0 AND beta_DISCOVERY_C1>0",
            "T2": "pooled discovery bootstrap 95% CI lower>0",
            "T3": "BH q<=0.05 on m=270",
            "T4": "minute modulo 5, >=4/5 subgroup betas>0",
            "T5": "LOMO >=75% beta>0",
            "T6": "beta_60>0 AND CI_60 lower>0 AND beta_60>=0.50*beta_primary; cannot rescue T1-T5",
        },
        "fv_gates": {
            "F1": "beta_FV>0",
            "F2": "FV bootstrap 95% CI lower>0",
            "F3": ">=2/3 chronological FV folds beta>0",
            "F4": "FV LOMO >=75% beta>0",
            "F5": "beta_FV_60>0 AND CI_FV_60 lower>0 AND beta_FV_60>=0.50*beta_FV_primary",
            "F6": "BH q<=0.05 across frozen candidate n",
        },
        "coverage_gates": {
            "GLOBAL_standard_confirmed_ge": 11,
            "GLOBAL_distinct_sectors_ge": 4,
            "GLOBAL_max_sector_share": 0.40,
            "standard_subset": "pool N_PEER_PIT>=2",
            "fallback_cannot_rescue_global_coverage": True,
            "SECTOR_3650_confirmed_ge": 5,
        },
        "symbol_outcomes_opened_in_this_precommit": False,
    }
    base["precommit_sha256"] = sha256_obj(base)
    return base

"""Lock six information arms. No threshold / subset / date search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.am_entry_temporal_regime_information import (
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    CORE_STATE_FEATURES,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    REGIME_FEATURES,
    TEMPORAL_FEATURES,
)
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def extra_features_for(arm_id: str) -> tuple[str, ...]:
    from research.am_entry_temporal_regime_information import A0, A1, A2, A3, A4, A5

    if arm_id == A0:
        return ()
    if arm_id == A1:
        return TEMPORAL_FEATURES
    if arm_id == A2:
        return REGIME_FEATURES
    if arm_id == A3:
        return CORE_STATE_FEATURES
    if arm_id == A4:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES))
    if arm_id == A5:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES))
    return ()


def precommit_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SESSION": SESSION,
        "FROZEN_ARM": FROZEN_ARM,
        "TARGET": TARGET,
        "JOINT_SCORE": "P_WIN - P_LOSS",
        "X14_BUNDLE": list(X14_BUNDLE),
        "RF_CLF_PARAMS": dict(RF_CLF_PARAMS),
        "REPRESENTATION_N": int(REPRESENTATION_N),
        "REPRESENTATION_SELECTION": False,
        "POSITIVE_REP_MIN": int(POS_REP_MIN),
        "AVAILABLE_REP_MIN": int(POS_REP_MIN),
        "AUGMENT_MAX_PER_COHORT": int(AUGMENT_MAX_PER_COHORT),
        "CURRENT_PRIORITY": bool(CURRENT_PRIORITY),
        "CURRENT_TOPN": int(FINAL_SELECTION_N),
        "ARM_N": int(ARM_N),
        "ARM_IDS": list(ARM_IDS),
        "TEMPORAL_FEATURES": list(TEMPORAL_FEATURES),
        "REGIME_FEATURES": list(REGIME_FEATURES),
        "CORE_STATE_FEATURES": list(CORE_STATE_FEATURES),
        "APPEND_AFTER_REPRESENTATION": True,
        "DATE_FEATURE": False,
        "WEEKDAY_FEATURE": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "PROBABILITY_THRESHOLD_SEARCH": False,
        "REGIME_THRESHOLD_SEARCH": False,
        "TIME_WINDOW_SEARCH": False,
        "FEATURE_SUBSET_SEARCH": False,
        "HYPERPARAMETER_SEARCH": False,
        "NEW_MODEL": False,
        "NEW_CANDIDATE_FEATURE": False,
        "CURRENT_PNL_SIGN_GATE": False,
        "P_FILL_USE": False,
        "UTILITY_REGRESSION": False,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
    }


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED = true", flush=True)
    print(f"FROZEN_ARM = {spec['FROZEN_ARM']}", flush=True)
    print(f"TARGET = {spec['TARGET']}", flush=True)
    print(f"ARM_N = {spec['ARM_N']}", flush=True)
    print("A0 = X14_BASE", flush=True)
    print("A1 = X14_TEMPORAL", flush=True)
    print("A2 = X14_MARKET_REGIME", flush=True)
    print("A3 = X14_CORE_STATE", flush=True)
    print("A4 = X14_TEMPORAL_MARKET", flush=True)
    print("A5 = X14_ALL_REGIME", flush=True)
    print("APPEND_AFTER_REPRESENTATION = true", flush=True)
    print("DATE_FEATURE = false", flush=True)
    print("SCORE_THRESHOLD_SEARCH = false", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

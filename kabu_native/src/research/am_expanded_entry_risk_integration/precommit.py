"""Lock three semantic risk arms. No probability cutoff search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.am_expanded_entry_risk_integration import (
    ANALYSIS_ID,
    AUGMENT_MAX_PER_COHORT,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    RISK_ARM_IDS,
    RISK_ARM_N,
)
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def precommit_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SESSION": SESSION,
        "FROZEN_ARM": FROZEN_ARM,
        "TARGET": TARGET,
        "X14_BUNDLE": list(X14_BUNDLE),
        "RF_CLF_PARAMS": dict(RF_CLF_PARAMS),
        "REPRESENTATION_N": int(REPRESENTATION_N),
        "REPRESENTATION_SELECTION": False,
        "POSITIVE_REP_MIN": int(POS_REP_MIN),
        "AVAILABLE_REP_MIN": int(POS_REP_MIN),
        "AUGMENT_MAX_PER_COHORT": int(AUGMENT_MAX_PER_COHORT),
        "CURRENT_PRIORITY": bool(CURRENT_PRIORITY),
        "CURRENT_TOPN": int(FINAL_SELECTION_N),
        "RISK_ARM_N": int(RISK_ARM_N),
        "RISK_ARM_IDS": list(RISK_ARM_IDS),
        "SCORE_THRESHOLD_SEARCH": False,
        "PROBABILITY_THRESHOLD_SEARCH": False,
        "CONSENSUS_THRESHOLD_SEARCH": False,
        "FEATURE_SUBSET_SEARCH": False,
        "HYPERPARAMETER_SEARCH": False,
        "NEW_MODEL": False,
        "NEW_FEATURE": False,
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
    print(f"RISK_ARM_N = {spec['RISK_ARM_N']}", flush=True)
    print("R1 = WIN_DOMINANT", flush=True)
    print("R2 = CORE_CONFIRMED", flush=True)
    print("R3 = WIN_DOMINANT + CORE_CONFIRMED", flush=True)
    print("PROBABILITY_THRESHOLD_SEARCH = false", flush=True)
    print("SCORE_THRESHOLD_SEARCH = false", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

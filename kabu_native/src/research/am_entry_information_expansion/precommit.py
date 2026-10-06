"""Lock profitable-fill expansion contract before any fit. No post-hoc change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_information_expansion import (
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    INFO_BASE,
    INFO_X14,
    LOGIT_PARAMS,
    RF_CLF_PARAMS,
    SCORE_BOUNDARY,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def precommit_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SESSION": SESSION,
        "TARGET": TARGET,
        "JOINT_SCORE": "P_WIN - P_LOSS",
        "SCORE_BOUNDARY": float(SCORE_BOUNDARY),
        "ARM_N": int(ARM_N),
        "ARM_IDS": list(ARM_IDS),
        "INFORMATION_ARMS": [INFO_BASE, INFO_X14],
        "X14_BUNDLE": list(X14_BUNDLE),
        "X14_SUBSET_SEARCH": False,
        "REPRESENTATION_N": int(REPRESENTATION_N),
        "REPRESENTATION_SELECTION": False,
        "POSITIVE_REP_MIN": int(POS_REP_MIN),
        "AVAILABLE_REP_MIN": int(AVAILABLE_REP_MIN),
        "AUGMENT_MAX_PER_COHORT": int(AUGMENT_MAX_PER_COHORT),
        "CURRENT_PRIORITY": True,
        "CURRENT_TOPN": int(FINAL_SELECTION_N),
        "CURRENT_VETO": False,
        "CURRENT_REPLACEMENT": False,
        "P_FILL_USE": False,
        "UTILITY_REGRESSION": False,
        "LOGIT_PARAMS": dict(LOGIT_PARAMS),
        "RF_CLF_PARAMS": dict(RF_CLF_PARAMS),
        "HYPERPARAMETER_SEARCH": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
    }


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED = true", flush=True)
    print(f"TARGET = {spec['TARGET']}", flush=True)
    print("JOINT_SCORE = P_WIN - P_LOSS", flush=True)
    print(f"ARM_N = {spec['ARM_N']}", flush=True)
    print("X14_BUNDLE = frozen 6", flush=True)
    print("REPRESENTATION_SELECTION = false", flush=True)
    print("HYPERPARAMETER_SEARCH = false", flush=True)
    print("P_FILL_USE = false", flush=True)
    print(f"AUGMENT_MAX_PER_COHORT = {spec['AUGMENT_MAX_PER_COHORT']}", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

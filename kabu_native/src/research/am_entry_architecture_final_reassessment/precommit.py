"""Lock three consensus arms. No blend, no new model, no threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_architecture_final_reassessment import (
    ANALYSIS_ID,
    ARM_IDS,
    AUGMENT_MAX_PER_COHORT,
    B0,
    B1,
    C0,
    C1,
    C2,
    CONSENSUS_ARM_N,
    CURRENT_PRIORITY,
    FROZEN_ARM,
)
from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.am_entry_temporal_regime_information import CORE_STATE_FEATURES, REGIME_FEATURES, TEMPORAL_FEATURES
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


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
        "CONSENSUS_ARM_N": int(CONSENSUS_ARM_N),
        "ARM_IDS": list(ARM_IDS),
        "B0": B0,
        "B1": B1,
        "C0": C0,
        "C1": C1,
        "C2": C2,
        "C0_RULE": "B0_TOP1 then B1_ELIGIBLE confirm. No B1 rank. No fallback.",
        "C1_RULE": "B1_TOP1 then B0_ELIGIBLE confirm. No B0 rank. No fallback.",
        "C2_RULE": "Augment iff B0_TOP1 symbol == B1_TOP1 symbol. Else none.",
        "SCORE_BLEND": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "PROBABILITY_THRESHOLD_SEARCH": False,
        "NEW_MODEL": False,
        "NEW_FEATURE": False,
        "NEW_TARGET": False,
        "TEMPORAL_FEATURES": list(TEMPORAL_FEATURES),
        "REGIME_FEATURES": list(REGIME_FEATURES),
        "CORE_STATE_FEATURES": list(CORE_STATE_FEATURES),
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
        "TRUE_OOS": False,
        "NEW_FORWARD_N": 0,
    }


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED", spec.get("ANALYSIS_ID"), sha[:16], flush=True)
    print("CONSENSUS", spec.get("C0"), spec.get("C1"), spec.get("C2"), flush=True)

"""Lock four frozen arms. No descriptor subset / family / window search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.am_entry_temporal_regime_information import CORE_STATE_FEATURES, REGIME_FEATURES, TEMPORAL_FEATURES
from research.am_raw_event_incremental_selection import (
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    B0,
    B1,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    R0,
    R1,
    RAW_EVENT_AVAILABLE,
    RAW_WINDOW_SEC,
)
from research.raw_event_prediction_probe import RAW_DESCRIPTOR_N, RAW_DESCRIPTORS
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def extra_features_for(arm_id: str) -> tuple[str, ...]:
    raw = tuple((*RAW_DESCRIPTORS, RAW_EVENT_AVAILABLE))
    if arm_id == B0:
        return CORE_STATE_FEATURES
    if arm_id == R0:
        return tuple((*CORE_STATE_FEATURES, *raw))
    if arm_id == B1:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES))
    if arm_id == R1:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES, *raw))
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
        "RAW_DESCRIPTOR_N": int(RAW_DESCRIPTOR_N),
        "RAW_DESCRIPTORS": list(RAW_DESCRIPTORS),
        "RAW_EVENT_AVAILABLE": RAW_EVENT_AVAILABLE,
        "RAW_WINDOW_SEC": float(RAW_WINDOW_SEC),
        "TEMPORAL_FEATURES": list(TEMPORAL_FEATURES),
        "REGIME_FEATURES": list(REGIME_FEATURES),
        "CORE_STATE_FEATURES": list(CORE_STATE_FEATURES),
        "APPEND_AFTER_REPRESENTATION": True,
        "RAW_AFTER_SNAPSHOT_EXTRAS": True,
        "RAW_IN_CROSS_SECTIONAL_TRANSFORM": False,
        "RAW_DESCRIPTOR_SEARCH": False,
        "RAW_FAMILY_SEARCH": False,
        "RAW_WINDOW_SEARCH": False,
        "FEATURE_SUBSET_SEARCH": False,
        "HYPERPARAMETER_SEARCH": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "NEW_MODEL": False,
        "DATE_FEATURE": False,
        "WEEKDAY_FEATURE": False,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
        "B0_EXTRAS": list(extra_features_for(B0)),
        "R0_EXTRAS": list(extra_features_for(R0)),
        "B1_EXTRAS": list(extra_features_for(B1)),
        "R1_EXTRAS": list(extra_features_for(R1)),
    }


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED = true", flush=True)
    print(f"FROZEN_ARM = {spec['FROZEN_ARM']}", flush=True)
    print(f"TARGET = {spec['TARGET']}", flush=True)
    print(f"ARM_N = {spec['ARM_N']}", flush=True)
    print("B0 = X14_CORE_STATE", flush=True)
    print("R0 = X14_CORE_STATE_RAW23", flush=True)
    print("B1 = X14_ALL_REGIME", flush=True)
    print("R1 = X14_ALL_REGIME_RAW23", flush=True)
    print("RAW_DESCRIPTOR_N = 23", flush=True)
    print("RAW_WINDOW_SEC = 180", flush=True)
    print("APPEND_AFTER_REPRESENTATION = true", flush=True)
    print("SCORE_THRESHOLD_SEARCH = false", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

"""Lock four frozen arms. No sequence / GRU / window search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, SESSION
from research.am_entry_temporal_regime_information import CORE_STATE_FEATURES, REGIME_FEATURES, TEMPORAL_FEATURES
from research.am_event_sequence_architecture import (
    ADAMW_LR,
    ADAMW_WD,
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    B0,
    B1,
    CHANNELS,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    GRAD_CLIP,
    GRU_BATCH,
    GRU_BIDIRECTIONAL,
    GRU_EPOCHS,
    GRU_HIDDEN,
    GRU_LAYERS,
    GRU_SEED,
    S0,
    S1,
    SEQ_POSTERIOR3,
    SEQUENCE_CHANNEL_N,
    SEQUENCE_LEN,
    SEQUENCE_MODEL,
    SEQUENCE_WINDOW_SEC,
)
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def extra_features_for(arm_id: str) -> tuple[str, ...]:
    seq = tuple(SEQ_POSTERIOR3)
    if arm_id == B0:
        return CORE_STATE_FEATURES
    if arm_id == S0:
        return tuple((*CORE_STATE_FEATURES, *seq))
    if arm_id == B1:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES))
    if arm_id == S1:
        return tuple((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES, *seq))
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
        "SEQUENCE_LEN": int(SEQUENCE_LEN),
        "SEQUENCE_CHANNEL_N": int(SEQUENCE_CHANNEL_N),
        "SEQUENCE_MODEL": SEQUENCE_MODEL,
        "SEQUENCE_WINDOW_SEC": float(SEQUENCE_WINDOW_SEC),
        "CHANNELS": list(CHANNELS),
        "SEQ_POSTERIOR3": list(SEQ_POSTERIOR3),
        "GRU_HIDDEN": int(GRU_HIDDEN),
        "GRU_LAYERS": int(GRU_LAYERS),
        "GRU_BIDIRECTIONAL": bool(GRU_BIDIRECTIONAL),
        "ADAMW_LR": float(ADAMW_LR),
        "ADAMW_WD": float(ADAMW_WD),
        "GRU_EPOCHS": int(GRU_EPOCHS),
        "GRU_BATCH": int(GRU_BATCH),
        "GRAD_CLIP": float(GRAD_CLIP),
        "GRU_SEED": int(GRU_SEED),
        "APPEND_AFTER_REPRESENTATION": True,
        "SEQ_AFTER_SNAPSHOT_EXTRAS": True,
        "SEQ_IN_CROSS_SECTIONAL_TRANSFORM": False,
        "RAW23_REUSE": False,
        "SEQUENCE_CHANNEL_SEARCH": False,
        "SEQUENCE_WINDOW_SEARCH": False,
        "SEQUENCE_BIN_SEARCH": False,
        "MODEL_SEARCH": False,
        "HYPERPARAMETER_SEARCH": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "DATE_FEATURE": False,
        "WEEKDAY_FEATURE": False,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
        "B0_EXTRAS": list(extra_features_for(B0)),
        "S0_EXTRAS": list(extra_features_for(S0)),
        "B1_EXTRAS": list(extra_features_for(B1)),
        "S1_EXTRAS": list(extra_features_for(S1)),
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
    print("S0 = X14_CORE_STATE_SEQ", flush=True)
    print("B1 = X14_ALL_REGIME", flush=True)
    print("S1 = X14_ALL_REGIME_SEQ", flush=True)
    print("SEQUENCE_MODEL = CAUSAL_GRU16", flush=True)
    print("SEQUENCE_LEN = 180", flush=True)
    print("SEQUENCE_CHANNEL_N = 11", flush=True)
    print("RAW23_REUSE = false", flush=True)
    print("APPEND_AFTER_REPRESENTATION = true", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

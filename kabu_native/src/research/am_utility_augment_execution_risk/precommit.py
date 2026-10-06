"""Lock P_FILL-first utility-eligible augment before any fit. No post-hoc change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_current_utility_augment import (
    ARCHITECTURE,
    AUG_SCORE_THRESHOLD,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CURRENT_PRIORITY,
    ENSEMBLE,
    TARGET,
)
from research.am_entry_profit_improvement import (
    DEV_WAIT_SEC,
    FILL_ONLY_REPRESENTATION_ID,
    FINAL_SELECTION_N,
    REPRESENTATION_N,
    RIDGE_ALPHA,
    SESSION,
)
from research.am_utility_augment_execution_risk import ANALYSIS_ID, AUGMENT_RANK
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_primary_runtime import WAIT_SEC


def precommit_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "SESSION": SESSION,
        "ARCHITECTURE": ARCHITECTURE,
        "RIDGE_ALPHA": float(RIDGE_ALPHA),
        "REPRESENTATION_N": int(REPRESENTATION_N),
        "ENSEMBLE": ENSEMBLE,
        "AUG_SCORE_THRESHOLD": float(AUG_SCORE_THRESHOLD),
        "POSITIVE_REP_MIN": int(POS_REP_MIN),
        "AVAILABLE_REP_MIN": int(AVAILABLE_REP_MIN),
        "AUGMENT_MAX_PER_COHORT": int(AUGMENT_MAX_PER_COHORT),
        "CURRENT_PRIORITY": bool(CURRENT_PRIORITY),
        "CURRENT_TOPN": int(FINAL_SELECTION_N),
        "AUGMENT_RANK": AUGMENT_RANK,
        "PRIMARY_ORDER": "P_FILL5_DESC",
        "TIE1": "AUG_SCORE_DESC",
        "TIE2": "POSITIVE_REP_N_DESC",
        "TIE3": "SYMBOL_ASC",
        "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
        "P_FILL_CUTOFF": None,
        "P_FILL_THRESHOLD_SEARCH": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "WEIGHTED_UTILITY_PFILL": False,
        "STAGE2": False,
        "CURRENT_VETO": False,
        "CURRENT_REPLACEMENT": False,
        "REPRESENTATION_SELECTION": False,
        "TARGET": TARGET,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
    }


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED = true", flush=True)
    print(f"ARCHITECTURE = {spec['ARCHITECTURE']}", flush=True)
    print(f"RIDGE_ALPHA = {spec['RIDGE_ALPHA']}", flush=True)
    print(f"REPRESENTATION_N = {spec['REPRESENTATION_N']}", flush=True)
    print(f"ENSEMBLE = {spec['ENSEMBLE']}", flush=True)
    print("UTILITY_ELIGIBILITY = FROZEN", flush=True)
    print(f"AUGMENT_RANK = {spec['AUGMENT_RANK']}", flush=True)
    print(f"FILL_ONLY_REPRESENTATION_ID = {spec['FILL_ONLY_REPRESENTATION_ID']}", flush=True)
    print("P_FILL_CUTOFF = none", flush=True)
    print(f"AUGMENT_MAX_PER_COHORT = {spec['AUGMENT_MAX_PER_COHORT']}", flush=True)
    print(f"CURRENT_PRIORITY = {spec['CURRENT_PRIORITY']}", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

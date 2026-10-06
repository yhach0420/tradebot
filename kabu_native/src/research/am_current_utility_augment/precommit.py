"""Lock augment contract before any fit. No post-hoc change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_current_utility_augment import (
    ANALYSIS_ID,
    ARCHITECTURE,
    AUG_SCORE_THRESHOLD,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CURRENT_PRIORITY,
    ENSEMBLE,
    TARGET,
)
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N, REPRESENTATION_N, RIDGE_ALPHA, SESSION
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
        "TARGET": TARGET,
        "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
        "RUNTIME_WAIT_SEC": float(WAIT_SEC),
        "REPRESENTATION_SELECTION": False,
        "SCORE_THRESHOLD_SEARCH": False,
        "CURRENT_VETO": False,
        "CURRENT_REPLACEMENT": False,
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
    print(f"AUG_SCORE_THRESHOLD = {spec['AUG_SCORE_THRESHOLD']}", flush=True)
    print(f"POSITIVE_REP_MIN = {spec['POSITIVE_REP_MIN']}", flush=True)
    print(f"AVAILABLE_REP_MIN = {spec['AVAILABLE_REP_MIN']}", flush=True)
    print(f"AUGMENT_MAX_PER_COHORT = {spec['AUGMENT_MAX_PER_COHORT']}", flush=True)
    print(f"CURRENT_PRIORITY = {spec['CURRENT_PRIORITY']}", flush=True)
    print(f"PRECOMMIT_SPEC_SHA256 = {sha}", flush=True)

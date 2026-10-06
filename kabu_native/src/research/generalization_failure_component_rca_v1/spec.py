"""Pinned component-RCA contract. Coverage-corrected. No new economics. No temporal split."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.generalization_failure_component_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    COMPONENT_STRUCTURE,
    EXPECTED_LOW_SUPPORT_N,
    EXPECTED_VALID_N,
    L1_WIDE_SELECTION_INSTABILITY_PROVEN,
    L2_SELECTION_INSTABILITY_PROVEN,
    LOW_SUPPORT_IDS,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_REPLAY,
    NEXT_IF_A,
    ORIGINAL_G1G2_N,
    PARENT_MODE_REQUIRED,
    PARENT_NEXT_REQUIRED,
    PARENT_VERDICT_REQUIRED,
    PRIMARY_COMPONENT,
    RAW_CAPTURE_READ_N,
    RESIDUAL_BOTTLENECK,
    SELECTION_FORMALLY_TESTED_STUDY_IDS,
    ST_WINNER_ID,
    TEMPORAL_SPLIT,
)
from research.simple_full_strategy_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "sources.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "EXISTING_ARTIFACT_RCA_ONLY": True,
        "NEW_REPLAY": NEW_REPLAY,
        "NEW_ENTRY": NEW_ENTRY,
        "NEW_EXIT": NEW_EXIT,
        "NEW_CANDIDATE": NEW_CANDIDATE,
        "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
        "COVERAGE_FROM_ORIGINAL_GATE": True,
        "COVERAGE_INFER_FROM_TRADE_N_ONLY": False,
        "COVERAGE_TRADE_N_MIN": 40,
        "COVERAGE_TRADING_DAY_WITH_FILL_N_MIN": 8,
        "COVERAGE_TRADES_PER_DAY_MIN": 4.0,
        "ORIGINAL_G1G2_N": ORIGINAL_G1G2_N,
        "EXPECTED_LOW_SUPPORT_N": EXPECTED_LOW_SUPPORT_N,
        "EXPECTED_VALID_N": EXPECTED_VALID_N,
        "LOW_SUPPORT_IDS": list(LOW_SUPPORT_IDS),
        "LOW_SUPPORT_CANNOT_CHANGE_VERDICT": True,
        "ST_WINNER_ID": ST_WINNER_ID,
        "PARENT_VERDICT_REQUIRED": PARENT_VERDICT_REQUIRED,
        "PARENT_MODE_REQUIRED": PARENT_MODE_REQUIRED,
        "PARENT_NEXT_REQUIRED": PARENT_NEXT_REQUIRED,
        "STABILITY_NOT_RUN_IS_NOT_SELECTION_FAILURE": True,
        "RESIDUAL_REQUIRES_COVERAGE_AND_G1G6_AND_STABILITY_RAN": True,
        "L1_WIDE_SELECTION_INSTABILITY_PROVEN": L1_WIDE_SELECTION_INSTABILITY_PROVEN,
        "L2_SELECTION_INSTABILITY_PROVEN": L2_SELECTION_INSTABILITY_PROVEN,
        "SELECTION_FORMALLY_TESTED_STUDY_IDS": list(SELECTION_FORMALLY_TESTED_STUDY_IDS),
        "COMPONENT_STRUCTURE": COMPONENT_STRUCTURE,
        "PRIMARY_COMPONENT": PRIMARY_COMPONENT,
        "RESIDUAL_BOTTLENECK": RESIDUAL_BOTTLENECK,
        "CASE_A": CASE_A,
        "NEXT_IF_A": NEXT_IF_A,
        "TEMPORAL_SPLIT": TEMPORAL_SPLIT,
        "FIRST_3_BLOCK_FORBIDDEN": True,
        "LAST_2_BLOCK_FORBIDDEN": True,
        "KEEP_INDIVIDUAL_B1_B5": True,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "NEXT_SCOPE": "SYSTEMATIC_STATE_TRANSITION FORMAL BLOCKED-STABILITY MECHANISM RCA",
        "NEXT_QUESTION": "WHY DOES THE ONLY G1-G6-PASS ST STRATEGY FAIL TO TRANSFER ACROSS BLOCKED TRAIN SELECTION?",
        "FILTERS_FORBIDDEN": True,
    }


def spec_sha256() -> str:
    body = json.dumps(canonical_spec(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()

"""Pinned RCA contract. Existing artifacts only. No new economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.generalization_failure_rca_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CASE_F,
    E4_CAN_CHANGE_CASE,
    E4_PRIMARY_VOTE,
    EXPECTED_C1_G1G2_N,
    EXPECTED_RECOVERY_G1G2_N,
    EXPECTED_ST_G1G2_N,
    L3_PRIMARY_VOTE,
    NEW_CANDIDATE,
    NEW_ENTRY,
    NEW_EXIT,
    NEW_FOLD_RUN,
    NEW_FULL_CAUSAL_RUN,
    NEW_PNL_SIMULATION,
    NEW_REPLAY,
    NEW_THRESHOLD,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_C,
    NEXT_IF_D,
    NEXT_IF_E,
    NEXT_IF_F,
    PRIMARY_E1_STRATEGY_N,
    RAW_CAPTURE_READ_N,
    REBASE_DEFICIENCY_REQUIRED,
    REBASE_NEXT_REQUIRED,
    REBASE_VERDICT_REQUIRED,
    ST_WINNER_ID,
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
        "NEW_FULL_CAUSAL_RUN": NEW_FULL_CAUSAL_RUN,
        "NEW_PNL_SIMULATION": NEW_PNL_SIMULATION,
        "NEW_FOLD_RUN": NEW_FOLD_RUN,
        "NEW_ENTRY": NEW_ENTRY,
        "NEW_EXIT": NEW_EXIT,
        "NEW_THRESHOLD": NEW_THRESHOLD,
        "NEW_CANDIDATE": NEW_CANDIDATE,
        "RAW_CAPTURE_READ_N": RAW_CAPTURE_READ_N,
        "PRIMARY_E1_STRATEGY_N": PRIMARY_E1_STRATEGY_N,
        "EXPECTED_ST_G1G2_N": EXPECTED_ST_G1G2_N,
        "EXPECTED_C1_G1G2_N": EXPECTED_C1_G1G2_N,
        "EXPECTED_RECOVERY_G1G2_N": EXPECTED_RECOVERY_G1G2_N,
        "E4_PRIMARY_VOTE": E4_PRIMARY_VOTE,
        "L3_PRIMARY_VOTE": L3_PRIMARY_VOTE,
        "E4_CAN_CHANGE_CASE": E4_CAN_CHANGE_CASE,
        "ST_WINNER_ID": ST_WINNER_ID,
        "REBASE_VERDICT_REQUIRED": REBASE_VERDICT_REQUIRED,
        "REBASE_DEFICIENCY_REQUIRED": REBASE_DEFICIENCY_REQUIRED,
        "REBASE_NEXT_REQUIRED": REBASE_NEXT_REQUIRED,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_D": CASE_D,
        "CASE_E": CASE_E,
        "CASE_F": CASE_F,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "NEXT_IF_C": NEXT_IF_C,
        "NEXT_IF_D": NEXT_IF_D,
        "NEXT_IF_E": NEXT_IF_E,
        "NEXT_IF_F": NEXT_IF_F,
        "G6_FALSE_WITHOUT_RUN_IS_NOT_FAILURE": True,
        "SAME_SYMBOL_ID_NOT_REQUIRED_FOR_SYMBOL_MODE": True,
        "SAME_DAY_ID_NOT_REQUIRED_FOR_DAY_MODE": True,
        "D3_CANNOT_OVERRIDE_CASE": True,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS_SEALED": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "REVIVE_FORBIDDEN": [
            "REVIVE_E4",
            "REVIVE_RECOVERY",
            "REVIVE_STATE_TRANSITION",
            "REVIVE_C1",
            "REVIVE_C4",
            "REVIVE_X9",
        ],
        "FILTERS_FORBIDDEN": [
            "SYMBOL_FILTER_CREATED",
            "DATE_FILTER_CREATED",
            "REGIME_GATE_CREATED",
            "TIME_GATE_CREATED",
            "WEEKDAY_RULE_CREATED",
        ],
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

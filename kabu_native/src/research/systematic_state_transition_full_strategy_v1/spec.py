"""Pinned Full Strategy contract. Canary R2_X1_Z3. G6 diagnostic only. No E4_X3 canary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.systematic_state_transition_full_strategy_v1 import (
    ANALYSIS_ID,
    CANARY_EXPECTED,
    CANARY_ID,
    CANARY_OPTIONAL,
    CANARY_SOURCE,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_CANARY,
    CASE_D,
    CASE_E,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    LEGACY_E4_X3_CANARY,
    MAX_RESEARCH_DATE,
    POSITION_CAP,
    SELECTED_STRATEGY_SYMBOL_FILTER,
    SHARES,
    TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
)
from research.systematic_state_transition_library_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.systematic_state_transition_library_precommit_v1.library import build_raw_library
from research.systematic_state_transition_library_precommit_v1.spec import (
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    exit_contract,
    fold_assignment,
    portfolio_contract,
    stability_gates,
)
from research.systematic_state_transition_library_precommit_v1.state_registry import build_state_registry

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "states.py",
    "entries.py",
    "harvest.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def frozen_library() -> list[dict[str, Any]]:
    raw = build_raw_library()
    dup = audit_raw_library(raw)
    final, _counts = apply_prune(raw, dup)
    if len(final) != 25:
        raise RuntimeError("FROZEN_LIBRARY_N")
    return final


def candidate_ids() -> list[str]:
    return [str(r["CANDIDATE_ID"]) for r in frozen_library()]


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_METRIC": "FULL_CAUSAL_PORTFOLIO_ECONOMICS",
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "LEGACY_E4_X3_CANARY": LEGACY_E4_X3_CANARY,
        "CANARY_EXECUTION": "X1_IMMEDIATE_ASK from recovery_sequence_full_strategy_architecture_v1.execution",
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "CANARY_OPTIONAL": dict(CANARY_OPTIONAL),
        "CANDIDATE_N": 25,
        "CANDIDATE_IDS": candidate_ids(),
        "ENTRY_TEMPLATES": ["PERSIST_NEXT", "HANDOFF_NEXT"],
        "EXECUTION": execution_contract(),
        "EXIT": exit_contract(),
        "PORTFOLIO": portfolio_contract(),
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "FOLD_BLOCKS": {k: list(v) for k, v in FOLD_BLOCKS.items()},
        "COVERAGE_GATES": coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "FOLD_ASSIGNMENT": fold_assignment(),
        "SELECTED_STRATEGY_SYMBOL_FILTER": SELECTED_STRATEGY_SYMBOL_FILTER,
        "TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY": TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
        "CAUSAL_EX_TOP1_PART_OF_STRATEGY": False,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "STRESS_SEALED": True,
        "HOLDOUT_SEALED": True,
        "SIZING": False,
        "STATIC_AND_GRID": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_C": CASE_C,
        "CASE_D": CASE_D,
        "CASE_E": CASE_E,
        "CASE_CANARY": CASE_CANARY,
        "STATE_REGISTRY_SHA256": dumps_sha256(build_state_registry()),
        "FINAL_CANDIDATE_LIBRARY_SHA256": dumps_sha256(frozen_library()),
    }


def spec_sha256(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec or canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()

"""Pinned rethink contract. Class selection only. No rules, libraries, thresholds, or economics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.new_architecture_class_rethink_v1 import (
    ANALYSIS_ID,
    BURNED_HOLDOUT_DAYS,
    CASE_A,
    CASE_B,
    CASE_E,
    CLASS_IDS,
    DEVELOPMENT_DAYS,
    ELIGIBILITY_FIELDS,
    MAX_RESEARCH_DATE,
    NEXT_IF_B,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
    POSITION_CAP,
    SESSION,
    SHARES,
    STRESS_DAYS,
)
from research.systematic_state_transition_library_precommit_v1.spec import (
    execution_contract,
    exit_contract,
    fold_assignment,
    portfolio_contract,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "feasibility.py",
    "classes.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selection_priority() -> list[str]:
    return [
        "structurally distinct from all closed classes",
        "exact causal inputs already exist",
        "can define a finite precommit without parameter search",
        "can run ENTRY+EXEC+EXIT+CAP+slot as Full Causal",
        "directly advances primary objective: broad-enough ENTRY population + failure processing + portfolio economics",
        "least semantic reconstruction",
        "least new parameter creation",
        "architecture_class_id alphabetical tie",
    ]


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ARCHITECTURE_CLASSES": list(CLASS_IDS),
        "FIFTH_CLASS_FORBIDDEN": True,
        "ELIGIBILITY_FIELDS": list(ELIGIBILITY_FIELDS),
        "SELECTION_PRIORITY": selection_priority(),
        "PNL_USED_TO_SELECT_CLASS": False,
        "EVENT_COUNT_USED_TO_SELECT_CLASS": False,
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
        "NEW_TRIGGER_DEFINITION_N": 0,
        "NEW_THRESHOLD_DEFINITION_N": 0,
        "ESTIMATED_EVENT_N_FORBIDDEN": True,
        "EXACT_ENTRY_RULE_CREATION": False,
        "EXACT_EXIT_RULE_CREATION": False,
        "CANDIDATE_LIBRARY_GENERATION": False,
        "THRESHOLD_SELECTION": False,
        "TIMEFRAME_SELECTION_BY_ECONOMICS": False,
        "AGE_THRESHOLD_SELECTION": False,
        "CROWDING_THRESHOLD_SELECTION": False,
        "LABEL_GUIDED_FEATURE_SELECTION": False,
        "FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER": False,
        "FUTURE_EPISODE_ENDPOINT_USED": False,
        "CAP_QUALITY_TOPK_FORBIDDEN": True,
        "SYMBOL_RANKING_FORBIDDEN": True,
        "POSTHOC_WINNER_SYMBOL_FORBIDDEN": True,
        "STATIC_AND_GRID": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "ECONOMICS_RUN": False,
        "SIZING": False,
        "STRESS_OPEN": False,
        "FUTURE_DATA": False,
        "PFQ_REVIVAL": False,
        "PFQ_RECON_RUN": False,
        "PFQ_RECON_FORBIDDEN": PFQ_RECON_FORBIDDEN,
        "PFQ_DOCUMENT_ID": PFQ_DOCUMENT_ID,
        "PFQ_VERDICT": PFQ_VERDICT,
        "PFQ_LINE": PFQ_LINE,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_REVIVAL": False,
        "OR_ECONOMICS_OPENED": False,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "ARTIFACT_CREATED_AFTER_20260807_ALLOWED": True,
        "NEW_MARKET_DATA_AFTER_20260807_ALLOWED": False,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS": list(STRESS_DAYS),
        "SESSION": SESSION,
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "EXECUTION": execution_contract(),
        "EXIT": exit_contract(),
        "PORTFOLIO": portfolio_contract(),
        "FOLDS": fold_assignment(),
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_E": CASE_E,
        "NEXT_IF_B": NEXT_IF_B,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def spec_sha256() -> str:
    return dumps_sha256(canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes())
    return h.hexdigest()

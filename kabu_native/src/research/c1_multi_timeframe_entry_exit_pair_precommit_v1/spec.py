"""Pinned pair-precommit contract. No economics. No new EXIT. No ENTRY change."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    BURNED_HOLDOUT_DAYS,
    C4_CLASS,
    CASE_A,
    CASE_E,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXIT_IDS,
    HTF_IDS,
    HTF_WIDTH_SEC,
    MAX_RAW_STRATEGY_N,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_E,
    NEXT_IF_ZERO_ROBUST_PAIRS,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
    POSITION_CAP,
    PRIOR_ANALYSIS_ID,
    PRIOR_VERDICT_REQUIRED,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    PRIOR_Z3_ONLY_NEXT,
    RAW_CANDIDATE_IDS,
    RAW_STRATEGY_N,
    SESSION,
    SHARES,
    SIMPLE_FULL_X1_ID,
    STATE_IDS,
    STRESS_DAYS,
)
from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec as c1_canary_spec,
    coverage_gates,
    economic_gates,
    exact_entry_rule,
    execution_contract as c1_execution_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior.py",
    "exits_registry.py",
    "library.py",
    "duplicate_audit.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution_contract() -> dict[str, Any]:
    e = dict(c1_execution_contract())
    if str(e.get("EXEC_ID") or "") != EXECUTION_ID:
        raise RuntimeError("EXECUTION_ID_MISMATCH")
    return e


def pair_id(entry_id: str, exit_id: str) -> str:
    return f"{entry_id}__{EXECUTION_ID}__{exit_id}"


def pair_interaction_diagnostics_contract() -> dict[str, Any]:
    return {
        "RUN_THIS_PRECOMMIT": False,
        "DESCRIPTIVE_ONLY": True,
        "PER_ENTRY_REPORT": [
            "5 EXIT PnLs",
            "5 EXIT PFs",
            "trade_n range",
            "holding-time range",
            "slot-release range",
            "downstream-admission N/PnL range",
        ],
        "PER_EXIT_REPORT": "results across all 10 ENTRYs",
        "EXIT6_FROM_INTERACTION_FORBIDDEN": True,
    }


def next_full_strategy_rule() -> dict[str, Any]:
    return {
        "SELECTION_UNIT": "ENTRY×EXIT PAIR",
        "ENTRY_ONLY_DECISION": False,
        "SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION": False,
        "Z3_ONLY_C1_CLOSURE_FORBIDDEN": True,
        "BEST_ENTRY_THEN_EXIT_COMPARE_FORBIDDEN": True,
        "FULL_CAUSAL_OCCUPANCY_REQUIRED": True,
        "IF_ROBUST_STABLE_PAIR_N_EQ_0": NEXT_IF_ZERO_ROBUST_PAIRS,
        "C1_DEDICATED_EXIT_INVENTION_FORBIDDEN": True,
        "EXIT_THRESHOLD_RETUNE_FORBIDDEN": True,
        "TIMEFRAME_RETUNE_FORBIDDEN": True,
        "ENTRY_RESCUE_FORBIDDEN": True,
    }


def methodology_contract() -> dict[str, Any]:
    return {
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIMARY_DECISION_UNIT": "FULL_CAUSAL_STRATEGY_PAIR",
        "ENTRY_ONLY_DECISION": False,
        "SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION": False,
        "Z3_ONLY_C1_CLOSURE_FORBIDDEN": True,
        "CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": True,
        "TECHNICAL_EXIT_NEW_SEARCH": False,
        "NEW_EXIT_RULE_N": 0,
        "EXIT_THRESHOLD_RETUNE_N": 0,
        "EXIT6_CREATED": False,
        "EXIT_PARAMETER_CHANGED": False,
        "EXIT_IMPLEMENTATION_CHANGED": False,
        "NEW_EXIT_CREATED": False,
        "EXECUTION_ID_AMBIGUITY": False,
        "BARE_X1_FORBIDDEN": True,
        "SIMPLE_FULL_X1_IS_DIFFERENT": SIMPLE_FULL_X1_ID,
        "NEXT_FULL_STRATEGY_CANNOT_OPEN_UNLESS_CANARY_PARITY": True,
        "PRIOR_Z3_ONLY_NEXT_SUPERSEDED": True,
        "PRIOR_Z3_ONLY_NEXT": PRIOR_Z3_ONLY_NEXT,
        "PRIOR_Z3_ONLY_ANALYSIS_ID": PRIOR_Z3_ONLY_ANALYSIS_ID,
        "OLD_ENTRY_PLUS_ZI_FAILURE_DOES_NOT_AUTO_EXCLUDE_PAIR": True,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
    }


def canary_spec() -> dict[str, Any]:
    return dict(c1_canary_spec())


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "PRIMARY_DECISION_UNIT": "FULL_CAUSAL_STRATEGY_PAIR",
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
        "SAME_FAMILY_ONLY": True,
        "CROSS_FAMILY_GRID": False,
        "CROSS_FAMILY_ENTRY": False,
        "VWAP_GLOBAL_GATE": False,
        "HTF_IDS": list(HTF_IDS),
        "HTF_WIDTH_SEC": dict(HTF_WIDTH_SEC),
        "STATE_IDS": list(STATE_IDS),
        "FROZEN_ENTRY_IDS": list(RAW_CANDIDATE_IDS),
        "FROZEN_ENTRY_N": len(RAW_CANDIDATE_IDS),
        "EXECUTION_ID": EXECUTION_ID,
        "BARE_X1_FORBIDDEN": True,
        "SIMPLE_FULL_X1_IS_DIFFERENT": SIMPLE_FULL_X1_ID,
        "EXIT_IDS": list(EXIT_IDS),
        "EXIT_N": len(EXIT_IDS),
        "RAW_STRATEGY_N": int(RAW_STRATEGY_N),
        "MAX_RAW_STRATEGY_N": int(MAX_RAW_STRATEGY_N),
        "CANDIDATE_BACKFILL": False,
        "CANDIDATE11": False,
        "ENTRY_MODIFIED": False,
        "THRESHOLD_CHANGED": False,
        "TIMEFRAME_SELECTION_BY_ECONOMICS": False,
        "C4_CLASS": C4_CLASS,
        "C4_REMAINS_ELIGIBLE": True,
        "C4_EXECUTED_THIS_RUN": False,
        "C4_RULE_CREATED": False,
        "PRIOR_ANALYSIS_ID": PRIOR_ANALYSIS_ID,
        "PRIOR_VERDICT_REQUIRED": PRIOR_VERDICT_REQUIRED,
        "PRIOR_Z3_ONLY_NEXT_SUPERSEDED": True,
        "ECONOMICS_RUN": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "NEW_EXIT": False,
        "TECHNICAL_EXIT_NEW_SEARCH": False,
        "EXIT6_CREATED": False,
        "SIZING": False,
        "STRESS_OPEN": False,
        "FUTURE_DATA": False,
        "PFQ_REVIVAL": False,
        "PFQ_RECON_FORBIDDEN": PFQ_RECON_FORBIDDEN,
        "PFQ_DOCUMENT_ID": PFQ_DOCUMENT_ID,
        "PFQ_VERDICT": PFQ_VERDICT,
        "PFQ_LINE": PFQ_LINE,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS": list(STRESS_DAYS),
        "SESSION": SESSION,
        "SHARES": int(SHARES),
        "POSITION_CAP": int(POSITION_CAP),
        "EXACT_ENTRY_RULE": exact_entry_rule(),
        "EXECUTION": execution_contract(),
        "PORTFOLIO": portfolio_contract(),
        "FOLDS": fold_assignment(),
        "COVERAGE_GATES": coverage_gates(),
        "FOLD_COVERAGE_GATES": fold_coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "CANARY": canary_spec(),
        "METHODOLOGY": methodology_contract(),
        "NEXT_FULL_STRATEGY_RULE": next_full_strategy_rule(),
        "PAIR_INTERACTION_DIAGNOSTICS": pair_interaction_diagnostics_contract(),
        "CASE_A": CASE_A,
        "CASE_E": CASE_E,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_E": NEXT_IF_E,
        "NEXT_IF_ZERO_ROBUST_PAIRS": NEXT_IF_ZERO_ROBUST_PAIRS,
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
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

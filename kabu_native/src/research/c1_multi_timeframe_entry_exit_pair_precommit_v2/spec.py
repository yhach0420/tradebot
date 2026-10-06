"""Pinned V2 uniqueness-audit contract. No candidate economics. No new EXIT."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    economic_gates,
    exact_entry_rule,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    pair_id,
    portfolio_contract,
    stability_gates,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v2 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    CASE_A,
    CASE_B,
    CASE_E,
    DEV_CLASSIFICATION,
    DEVELOPMENT_DAYS,
    EXECUTION_ID,
    EXPECTED_EXIT_FILE_SHA256,
    EXIT_IDS,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    NEXT_IF_E,
    PAIRWISE_EXIT_PAIRS,
    POSITION_CAP,
    PRIOR_PAIR_ANALYSIS_ID,
    PRIOR_PAIR_VERDICT_REQUIRED,
    PRIOR_Z3_ONLY_ANALYSIS_ID,
    PRIOR_Z3_ONLY_VERDICT_REQUIRED,
    RAW_CANDIDATE_IDS,
    SESSION,
    SHARES,
    Z3_PREVIOUSLY_OBSERVED_PAIR_N,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior.py",
    "bar_contract.py",
    "exits_proof.py",
    "bars_load.py",
    "suffix_audit.py",
    "equivalence.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def dumps_sha256(obj: Any) -> str:
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "DEV_CLASSIFICATION": DEV_CLASSIFICATION,
        "PRECOMMIT_ALL_PAIRS_PRE_ECONOMICS": False,
        "PRIMARY_DECISION_UNIT": "FULL_CAUSAL_STRATEGY_PAIR",
        "STRATEGY_UNIT": "ENTRY+EXECUTION+EXIT+CAP+SLOT_RELEASE",
        "PRIOR_PAIR_ANALYSIS_ID": PRIOR_PAIR_ANALYSIS_ID,
        "PRIOR_PAIR_VERDICT_REQUIRED": PRIOR_PAIR_VERDICT_REQUIRED,
        "PRIOR_Z3_ONLY_ANALYSIS_ID": PRIOR_Z3_ONLY_ANALYSIS_ID,
        "PRIOR_Z3_ONLY_VERDICT_REQUIRED": PRIOR_Z3_ONLY_VERDICT_REQUIRED,
        "Z3_SLICE_PREVIOUSLY_OBSERVED": True,
        "Z3_PREVIOUSLY_OBSERVED_PAIR_N": int(Z3_PREVIOUSLY_OBSERVED_PAIR_N),
        "DO_NOT_REMOVE_Z3_BECAUSE_OF_PRIOR_RESULT": True,
        "DO_NOT_PROMOTE_EXIT_BECAUSE_Z3_FAILED": True,
        "FROZEN_ENTRY_IDS": list(RAW_CANDIDATE_IDS),
        "FROZEN_ENTRY_N": 10,
        "EXIT_IDS": list(EXIT_IDS),
        "EXIT_N": 5,
        "PAIRWISE_N": len(PAIRWISE_EXIT_PAIRS),
        "EXECUTION_ID": EXECUTION_ID,
        "EXPECTED_EXIT_FILE_SHA256": EXPECTED_EXIT_FILE_SHA256,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_FILL_COUNT_COMPUTED": False,
        "CANDIDATE_TRADE_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "NEW_EXIT": False,
        "EXIT6_CREATED": False,
        "ENTRY_MODIFIED": False,
        "EXECUTION_MODIFIED": False,
        "EXIT_IMPLEMENTATION_MODIFIED": False,
        "SELECTION_GATES_MODIFIED": False,
        "CANDIDATE_BACKFILL": False,
        "SIZING": False,
        "STRESS_OPEN": False,
        "FUTURE_DATA": False,
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
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
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_E": CASE_E,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "NEXT_IF_E": NEXT_IF_E,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "PAIR_ID_FN": "research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec.pair_id",
        "PAIR_ID_EXAMPLE": pair_id(RAW_CANDIDATE_IDS[0], EXIT_IDS[0]),
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

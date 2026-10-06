"""Pinned C4 V2 seen-set contract. No economics. No occupancy-k. No EXIT6."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.c4_portfolio_crowding_precommit_v1 import KEPT_EXIT_IDS
from research.c4_portfolio_crowding_precommit_v2 import (
    ANALYSIS_ID,
    C4_SCOPE,
    CASE_A,
    CASE_E,
    CASE_INSUFFICIENT,
    CONTROL_POLICY_ID,
    CORRECTION_CLASS,
    DEV_CLASSIFICATION,
    DEVELOPMENT_DAYS,
    ENTRY_N,
    EXECUTION_ID,
    EXIT_N,
    MAX_RESEARCH_DATE,
    MIN_INFORMATIVE_BLOCK_N,
    NEXT_IF_A,
    NEXT_IF_E,
    NEXT_IF_INSUFFICIENT,
    POSITION_CAP,
    SHARES,
    TREATMENT_POLICY_ID,
)
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID, CANARY_SOURCE
from research.systematic_state_transition_library_precommit_v1 import FOLD_BLOCKS

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior.py",
    "policy.py",
    "streams.py",
    "intervention.py",
    "library.py",
    "analyze.py",
    "publish.py",
    "__main__.py",
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def incremental_gate() -> dict[str, Any]:
    return {
        "I1": "TREATMENT_TOTAL_PNL > CONTROL_TOTAL_PNL",
        "I2": "TREATMENT_ROBUST_VALUE > CONTROL_ROBUST_VALUE",
        "ROBUST_VALUE": (
            "min(TOTAL_PNL/TRADE_N, EX_BEST_DAY_PNL/TRADE_N, CAUSAL_EX_TOP1_PNL/TRADE_N)"
        ),
        "C4_INCREMENTAL_SUPPORT": "I1 AND I2",
        "PERCENTAGE_THRESHOLD": False,
        "SEARCHED_DELTA": False,
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "CONTROL_ROBUST_VALUE_COMPUTED_FOR_ATTRIBUTION_EVEN_IF_CONTROL_FAILS_GATES": True,
        "UNCHANGED_FROM_V1": True,
        "RUN_THIS_PRECOMMIT": False,
    }


def attribution_stability_gates() -> dict[str, Any]:
    return {
        "A1": "WINNER_C4_INFORMATIVE_BLOCK_N >= 3",
        "A2": "WINNER_C4_DELTA_TOTAL_PNL over informative blocks > 0",
        "A3": "WINNER_C4_DELTA_POSITIVE_BLOCK_N > WINNER_C4_DELTA_NEGATIVE_BLOCK_N",
        "C4_DELTA_PNL": "Treatment PnL - Control PnL",
        "ZERO_INTERVENTION_BLOCKS": "C4_NONINFORMATIVE_BLOCK; expected delta 0; excluded from sign test",
        "INFORMATIVE_BLOCK": "C4_REJECT_N_BY_BLOCK > 0",
        "DELTA_MAGNITUDE_THRESHOLD": False,
        "OPTIMIZATION": False,
        "CONTROL_CANNOT_WIN": True,
        "RUN_THIS_PRECOMMIT": False,
    }


def held_out_delta_contract() -> dict[str, Any]:
    return {
        "DIAGNOSTIC_ONLY": True,
        "NEW_STABILITY_THRESHOLD_FROM_DELTA": False,
        "REPORT": [
            "TEST_TREATMENT_PNL",
            "TEST_CONTROL_PNL",
            "TEST_C4_DELTA_PNL",
            "TEST_C4_INTERVENTION_N",
        ],
        "FORMAL_STABILITY_GATES": "S1-S6 unchanged",
        "ATTRIBUTION_GATES": "A1-A3 on Full-DEV winner, not fold-selected delta",
        "RUN_THIS_PRECOMMIT": False,
    }


def fold_c4_contract() -> dict[str, Any]:
    folds = dict(fold_assignment())
    folds["classification"] = "REUSED_HISTORY_BLOCKED_STABILITY"
    folds["TRAINING_REQUIRES"] = ["coverage", "G1-G6", "C4_INCREMENTAL_SUPPORT"]
    folds["MATCHED_CONTROL_USES_SAME_TRAINING_DAYS"] = True
    folds["HELD_OUT_INFORMATION_FORBIDDEN_IN_TRAINING"] = True
    folds["HELD_OUT_DELTA"] = held_out_delta_contract()
    folds["blocks"] = {k: list(v) for k, v in FOLD_BLOCKS.items()}
    return folds


def adoption_contract() -> dict[str, Any]:
    return {
        "REQUIRES": [
            "absolute Coverage PASS",
            "G1-G6 PASS",
            "C4_INCREMENTAL_SUPPORT=true",
            "STABILITY_PASS=true",
            "C4_ATTRIBUTION_STABILITY_PASS=true",
        ],
        "CONTROL_CANNOT_WIN": True,
        "RUN_THIS_PRECOMMIT": False,
    }


def arm_id(entry_id: str, policy_id: str, exit_id: str) -> str:
    return f"{entry_id}__{policy_id}__{EXECUTION_ID}__{exit_id}"


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "DEV_CLASSIFICATION": DEV_CLASSIFICATION,
        "PRIMARY_DECISION_UNIT": "ENTRY+C4 admission+X1_IMMEDIATE_ASK+EXIT+CAP5+same-symbol+occupancy+slot release+reentry",
        "PRIMARY_GOAL": "semantic C4 V2 implementation + prune to intervention-eligible ENTRYs before economics",
        "CORRECTION_CLASS": CORRECTION_CLASS,
        "NOT_NEW_C4_ARCHITECTURE": True,
        "NOT_THRESHOLD_SEARCH": True,
        "NOT_RESULT_DRIVEN_RETUNE": True,
        "C4_IS_ADMISSION_OVERLAY": True,
        "ENTRY_N": int(ENTRY_N),
        "ALL_25_ENTRY_USED_BEFORE_PRUNE": True,
        "PRIOR_ST_WINNER_SPECIAL_TREATMENT": False,
        "ENTRY_PARAMETER_CHANGED": False,
        "EXIT_N": int(EXIT_N),
        "EXIT_IDS": list(KEPT_EXIT_IDS),
        "Z4_TRAILING_STRUCTURE_PRESENT": False,
        "EXIT6_CREATED": False,
        "EXECUTION_ID": EXECUTION_ID,
        "CONTROL_POLICY_ID": CONTROL_POLICY_ID,
        "TREATMENT_POLICY_ID": TREATMENT_POLICY_ID,
        "C4_SCOPE": C4_SCOPE,
        "EXACT_T0_ROUNDING": False,
        "FUTURE_SAME_T0_COUNT_REQUIRED": False,
        "SAME_T0_BACKFILL": False,
        "OCCUPANCY_K_SEARCH": False,
        "CAP_QUALITY_RANKING": False,
        "SYMBOL_RANKING": False,
        "CAP": int(POSITION_CAP),
        "SHARES": int(SHARES),
        "MIN_INFORMATIVE_BLOCK_N": int(MIN_INFORMATIVE_BLOCK_N),
        "CONTROL_ELIGIBLE_AS_WINNER": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CONTROL_ECONOMICS_RUN": False,
        "TREATMENT_ECONOMICS_RUN": False,
        "FILL_COUNT_COMPUTED": False,
        "TRADE_COUNT_COMPUTED": False,
        "PNL_COMPUTED": False,
        "PF_COMPUTED": False,
        "MAXDD_COMPUTED": False,
        "RANKING_COMPUTED": False,
        "FOLD_ECONOMICS_RUN": False,
        "EXECUTION": execution_contract(),
        "PORTFOLIO": portfolio_contract(),
        "COVERAGE_GATES": coverage_gates(),
        "FOLD_COVERAGE_GATES": fold_coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "INCREMENTAL_GATE": incremental_gate(),
        "ATTRIBUTION_STABILITY": attribution_stability_gates(),
        "ADOPTION": adoption_contract(),
        "FOLDS": fold_c4_contract(),
        "CANARY": canary_spec(),
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "CANARY_EXPECTED": dict(CANARY_EXPECTED),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "CASE_A": CASE_A,
        "CASE_INSUFFICIENT": CASE_INSUFFICIENT,
        "CASE_E": CASE_E,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_INSUFFICIENT": NEXT_IF_INSUFFICIENT,
        "NEXT_IF_E": NEXT_IF_E,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "SIZING": False,
    }


def spec_sha256() -> str:
    return dumps_sha256(canonical_spec())


def source_sha256() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        h.update(name.encode("utf-8"))
        h.update((root / name).read_bytes() if (root / name).is_file() else b"MISSING")
    return h.hexdigest()

"""Pinned C1 precommit contract. No economics. No cross-family grid. No new thresholds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.c1_multi_timeframe_precommit_v1 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    BURNED_HOLDOUT_DAYS,
    BUCKET_ORIGIN_LABEL,
    C4_CLASS,
    CASE_A,
    CASE_B,
    CASE_E,
    DEVELOPMENT_DAYS,
    HTF_IDS,
    HTF_WIDTH_SEC,
    MAX_RAW_CANDIDATE_N,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
    POSITION_CAP,
    PRIOR_ANALYSIS_ID,
    PRIOR_SELECTED_REQUIRED,
    PRIOR_VERDICT_REQUIRED,
    RAW_CANDIDATE_IDS,
    SESSION,
    SHARES,
    STATE_IDS,
    STRESS_DAYS,
)
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID, CANARY_SOURCE
from research.systematic_state_transition_library_precommit_v1.spec import (
    execution_contract as st_execution_contract,
    exit_contract,
    fold_assignment,
    portfolio_contract,
)


def execution_contract() -> dict[str, Any]:
    e = dict(st_execution_contract())
    e["QUOTE_FRESHNESS"] = {
        "ASK": "AskTime or canonical ingress fallback",
        "BID": "BidTime or canonical ingress fallback",
        "CURRENT_PRICE_TIME_FORBIDDEN": True,
    }
    return e

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "prior.py",
    "bucket_alignment.py",
    "asof_semantics.py",
    "vwap_semantics.py",
    "session_reset.py",
    "one_min_states.py",
    "htf_states.py",
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


def engine_event_order() -> list[str]:
    return [
        "raw ingress event arrives",
        "completed 1m/3m/5m buckets are finalized",
        "indicators are computed",
        "HTF state is published to as-of state store",
        "1m strategy evaluates ENTRY",
        "execution search begins",
    ]


def coverage_gates() -> dict[str, Any]:
    return {
        "TRADE_N_MIN": 40,
        "TRADING_DAY_WITH_FILL_N_MIN": 8,
        "TRADES_PER_DAY_MIN": 4.0,
        "RELAX_FORBIDDEN": True,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
    }


def fold_coverage_gates() -> dict[str, Any]:
    return {
        "TRAIN_DAYS": 8,
        "TRADE_N_MIN": 32,
        "TRADING_DAY_WITH_FILL_N_MIN": 7,
        "TRADES_PER_DAY_MIN": 4.0,
    }


def economic_gates() -> dict[str, Any]:
    return {
        "G1": "TOTAL_PNL > 0",
        "G2": "PF > 1.10",
        "G3": "positive_days > negative_days",
        "G4": "EX_BEST_DAY_PNL > 0",
        "G5": "TOTAL_PNL + MAXDD > 0",
        "G6": "CAUSAL_EX_TOP1_PNL >= 0",
        "CAUSAL_EX_TOP1": (
            "From candidate BASE ledger pick top net-PnL symbol; exclude that symbol "
            "from signal generation before rerun; Full Causal rerun. Posthoc subtraction forbidden. "
            "Exclusion is not part of strategy identity."
        ),
        "ROBUST_SCORE": (
            "minimum of TOTAL_PNL/TRADE_N, EX_BEST_DAY_PNL/TRADE_N, CAUSAL_EX_TOP1_PNL/TRADE_N; "
            "highest among hard-gate PASS candidates; tie higher PF then higher trade_n then candidate_id"
        ),
        "ALL_REQUIRED": True,
    }


def stability_gates() -> dict[str, Any]:
    return {
        "NAME": "STABILITY_PASS",
        "NOT_CALLED": ["VALIDATION_PASS", "OOS_PASS"],
        "S1_TRAIN_TOP3_N_MIN": ">=3/5",
        "S2_WINNER_BASE_POSITIVE_BLOCKS_MIN": ">=3/5",
        "S3_WINNER_EX_BEST_BLOCK_PNL": ">=0",
        "S4_FOLD_SELECTED_TEST_TOTAL_PNL": ">0",
        "S5_FOLD_SELECTED_POSITIVE_BLOCKS_MIN": ">=3/5",
        "S6_FOLD_SELECTED_EX_BEST_BLOCK_PNL": ">=0",
        "FOLD_TEST_SYMBOL_EXCLUSION_N": 0,
        "WINNER_BLOCK_SYMBOL_FILTER_N": 0,
        "ALL_REQUIRED": True,
        "NO_RETUNING_FROM_BLOCKS": True,
    }


def canary_spec() -> dict[str, Any]:
    return {
        "CANARY_ID": CANARY_ID,
        "CANARY_SOURCE": CANARY_SOURCE,
        "HARD_FAIL_BLOCKS_ECONOMICS": True,
        "EXPECTED": dict(CANARY_EXPECTED),
        "RUN_THIS_PRECOMMIT": False,
    }


def exact_entry_rule() -> str:
    return (
        "SIGNAL(S,H,t0) = 1m S transitions FALSE→TRUE at completed 1m bar i with t0=finalize_t[i], "
        "where S[i-1] is evaluable and FALSE and S[i] is evaluable and TRUE; AND latest completed "
        "same-family H state with H.finalize_t <= t0 and HTF state published before 1m strategy "
        "evaluation is evaluable and TRUE. No HTF onset, age, or persistence. Same-family only."
    )


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": False,
        "SAME_FAMILY_ONLY": True,
        "CROSS_FAMILY_GRID": False,
        "HTF_IDS": list(HTF_IDS),
        "HTF_WIDTH_SEC": dict(HTF_WIDTH_SEC),
        "BUCKET_ORIGIN": BUCKET_ORIGIN_LABEL,
        "SESSION_SCOPED": True,
        "STATE_IDS": list(STATE_IDS),
        "RAW_CANDIDATE_IDS": list(RAW_CANDIDATE_IDS),
        "MAX_RAW_CANDIDATE_N": int(MAX_RAW_CANDIDATE_N),
        "CANDIDATE_BACKFILL": False,
        "CANDIDATE11": False,
        "THRESHOLD_CHANGED": False,
        "TIMEFRAME_SELECTION_BY_ECONOMICS": False,
        "HTF_ONSET_REQUIRED": False,
        "HTF_AGE_REQUIRED": False,
        "HTF_PERSISTENCE_REQUIRED": False,
        "V7_SAME_BUCKET_JOIN_USED": False,
        "HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME": False,
        "HTF_VWAP_USES_CANONICAL_SESSION_VWAP": True,
        "ENGINE_EVENT_ORDER": engine_event_order(),
        "EXACT_ENTRY_RULE": exact_entry_rule(),
        "C4_CLASS": C4_CLASS,
        "C4_REMAINS_ELIGIBLE": True,
        "C4_EXECUTED_THIS_RUN": False,
        "C4_RULE_CREATED": False,
        "PRIOR_ANALYSIS_ID": PRIOR_ANALYSIS_ID,
        "PRIOR_VERDICT_REQUIRED": PRIOR_VERDICT_REQUIRED,
        "PRIOR_SELECTED_REQUIRED": PRIOR_SELECTED_REQUIRED,
        "STATIC_AND_GRID": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "ECONOMICS_RUN": False,
        "CANDIDATE_ECONOMICS_RUN": False,
        "CANDIDATE_SIGNAL_COUNT_COMPUTED": False,
        "CANDIDATE_PNL_COMPUTED": False,
        "NEW_EXIT": False,
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
        "EXECUTION": execution_contract(),
        "EXIT": exit_contract(),
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

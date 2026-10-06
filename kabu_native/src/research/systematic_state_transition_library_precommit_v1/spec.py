"""Pinned precommit contract. No economics. No 84-grid. No new thresholds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.systematic_state_transition_library_precommit_v1 import (
    ANALYSIS_ID,
    ARCHITECTURE_CLASS,
    BURNED_HOLDOUT_DAYS,
    CASE_A,
    CASE_B,
    CASE_E,
    DEVELOPMENT_DAYS,
    ENTRY_TEMPLATES,
    FOLD_BLOCKS,
    MAX_RESEARCH_DATE,
    NEXT_IF_A,
    NEXT_IF_B,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
    POSITION_CAP,
    SESSION,
    SHARES,
    STRESS_DAYS,
)

SOURCE_FILES = (
    "__init__.py",
    "spec.py",
    "isolation.py",
    "inventory_correction.py",
    "state_registry.py",
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
    return {
        "EXEC_ID": "X1_IMMEDIATE_ASK",
        "ORDER": "100-share marketable BUY",
        "TRIGGER": "first fresh valid Ask1 at/after signal t0",
        "FILL_PRICE": "observed Ask1",
        "FORBIDDEN": [
            "passive",
            "mid",
            "queue assumption",
            "trade print fill",
            "bar fill",
            "price improvement wait",
            "reprice",
            "chase",
        ],
        "SOURCE_PATH": "src/research/participation_onset_full_strategy_v1/execution.py",
        "SOURCE_FUNCTION": "evaluate_execution",
        "SEARCHED_THIS_RUN": False,
    }


def exit_contract() -> dict[str, Any]:
    return {
        "EXIT_ID": "Z3_TWO_BAR_WEAKNESS",
        "TIMEFRAME": "completed 1m bars",
        "EXACT_DEFINITION": (
            "first-fire of two consecutive completed 1m bars k where "
            "Close[k] < Open[k] AND Close[k] < Close[k-1]"
        ),
        "AFTER_TRIGGER": "EXIT_PENDING; first fresh valid Bid1; 100-share SELL; fill=Bid1",
        "SLOT": "held until actual EXIT fill",
        "SESSION_CLOSE": "operational exit",
        "SOURCE_PATH": "src/research/simple_full_strategy_discovery_v1/exits.py",
        "SOURCE_FUNCTION": "technical_fire_i exit_id==Z3",
        "SEARCHED_THIS_RUN": False,
        "NEW_EXIT_MINING": False,
    }


def portfolio_contract() -> dict[str, Any]:
    return {
        "SHARES": int(SHARES),
        "CAP": int(POSITION_CAP),
        "same_symbol": True,
        "occupancy": True,
        "slot_release": True,
        "reentry": True,
        "SESSION": SESSION,
        "event_time_causal": True,
        "SIZING": False,
    }


def fold_assignment() -> dict[str, Any]:
    return {
        "classification": "REUSED_HISTORY_BLOCKED_STABILITY",
        "BLOCKED_OOS": False,
        "INTERNAL_VALIDATION": False,
        "TRUE_OOS": False,
        "blocks": {k: list(v) for k, v in FOLD_BLOCKS.items()},
        "leave_one_2day_block_out_n": 5,
        "train_days_per_fold": 8,
        "no_retune_from_blocks": True,
    }


def coverage_gates() -> dict[str, Any]:
    return {
        "TRADE_N_MIN": 40,
        "TRADING_DAY_WITH_FILL_N_MIN": 8,
        "TRADES_PER_DAY_MIN": 4,
        "SYMBOL_N_HARD_MINIMUM": None,
        "SYMBOL_N_HARD_MINIMUM_ADDED": False,
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
            "from signal generation before rerun; Full Causal rerun. Posthoc subtraction forbidden."
        ),
        "ROBUST_SCORE": (
            "minimum of TOTAL_PNL/TRADE_N, EX_BEST_DAY_PNL/TRADE_N, CAUSAL_EX_TOP1_PNL/TRADE_N; "
            "highest among hard-gate PASS candidates"
        ),
    }


def stability_gates() -> dict[str, Any]:
    return {
        "NAME": "STABILITY_PASS",
        "NOT_CALLED": "VALIDATION_PASS",
        "TRAIN_TOP3_N_MIN": ">=3/5",
        "FOLD_SELECTED_TEST_TOTAL_PNL": ">0",
        "POSITIVE_BLOCKS_MIN": ">=3/5",
        "EX_BEST_BLOCK_TOTAL_PNL": ">=0",
        "NO_RETUNING_FROM_BLOCKS": True,
    }


def canonical_spec() -> dict[str, Any]:
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "ARCHITECTURE_CLASS": ARCHITECTURE_CLASS,
        "ENTRY_TEMPLATES": list(ENTRY_TEMPLATES),
        "STATIC_AND_GRID": False,
        "PRIOR_84_GRID_EXECUTED": False,
        "PRIOR_84_GRID_REJECTED_AS_REDUNDANT": True,
        "THRESHOLD_TUNED_THIS_RUN": False,
        "NEW_THRESHOLD": False,
        "RCI_THRESHOLD_RETUNE": False,
        "VOLUME_THRESHOLD_RETUNE": False,
        "BOARD_ALPHA": False,
        "SPREAD_FILTER": False,
        "SYMBOL_FILTER": False,
        "PRICE_FILTER": False,
        "NEW_EXIT": False,
        "PFQ_REVIVAL": False,
        "PFQ_RECON_RUN": False,
        "PFQ_RECON_FORBIDDEN": PFQ_RECON_FORBIDDEN,
        "PFQ_DOCUMENT_ID": PFQ_DOCUMENT_ID,
        "PFQ_VERDICT": PFQ_VERDICT,
        "PFQ_LINE": PFQ_LINE,
        "OR_REVIVAL": False,
        "OR_ECONOMICS_OPENED": False,
        "SIZING": False,
        "STRESS_OPEN": False,
        "FUTURE_DATA": False,
        "ECONOMICS_RUN": False,
        "SIGNAL_GENERATION": False,
        "TRADE_PNL": False,
        "FULL_CAUSAL_REPLAY": False,
        "CANDIDATE_BACKFILL": False,
        "MAX_RAW_CANDIDATE_N": 25,
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "DEVELOPMENT_CLASSIFICATION": "REUSED_HISTORY_DEVELOPMENT",
        "BURNED_HOLDOUT_DAYS": list(BURNED_HOLDOUT_DAYS),
        "STRESS_DAYS": list(STRESS_DAYS),
        "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
        "FOLD_BLOCKS": {k: list(v) for k, v in FOLD_BLOCKS.items()},
        "EXECUTION": execution_contract(),
        "EXIT": exit_contract(),
        "PORTFOLIO": portfolio_contract(),
        "COVERAGE_GATES": coverage_gates(),
        "ECONOMIC_GATES": economic_gates(),
        "STABILITY_GATES": stability_gates(),
        "CASE_A": CASE_A,
        "CASE_B": CASE_B,
        "CASE_E": CASE_E,
        "NEXT_IF_A": NEXT_IF_A,
        "NEXT_IF_B": NEXT_IF_B,
        "BLOCKED_OOS": False,
        "INTERNAL_VALIDATION": False,
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

"""Lock indicator-only EXIT architecture before any harvest/fit. No search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_c0_indicator_exit import (
    ANALYSIS_ID,
    ARCHITECTURE_ID,
    ENTRY_PARENT_ID,
    ENTRY_PARENT_SHA256,
    EXIT_FEATURES,
    EXIT_MODEL,
    EXIT_REASON,
    EXIT_SCOPE,
    EXIT_THRESHOLD,
    HOLDING_TIME_RULE_N,
    LOGREG_PARAMS,
    OUTER_FOLD_N,
    POSITION_FEATURES,
    REGIME_FEATURES,
    RESEARCH_PARALLELISM,
    SYMBOL_FEATURES,
    TERMINAL_REASON,
    TIME_FEATURE_N,
)
from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, SESSION
from research.am_entry_research_final_decision import PROSPECTIVE_CHALLENGER_NAME, PROSPECTIVE_STATUS
from small_paper.v1r_primary_runtime import WAIT_SEC


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def precommit_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "ARCHITECTURE_ID": ARCHITECTURE_ID,
            "ENTRY_PARENT_ID": ENTRY_PARENT_ID,
            "ENTRY_PARENT_NAME": PROSPECTIVE_CHALLENGER_NAME,
            "ENTRY_PARENT_STATUS": PROSPECTIVE_STATUS,
            "ENTRY_PARENT_SHA256": ENTRY_PARENT_SHA256,
            "EXIT_SCOPE": EXIT_SCOPE,
            "EXIT_MODEL": EXIT_MODEL,
            "EXIT_SIGNAL": "P_EXIT_BETTER > 0.5",
            "EXIT_THRESHOLD": float(EXIT_THRESHOLD),
            "TERMINAL": "AM_SESSION_END_ONLY",
            "TERMINAL_REASON": TERMINAL_REASON,
            "EXIT_REASON": EXIT_REASON,
            "TIME_BASED_EXIT": False,
            "TIME_FEATURE_N": int(TIME_FEATURE_N),
            "HOLDING_TIME_RULE_N": int(HOLDING_TIME_RULE_N),
            "C14_ROLE": "BENCHMARK_AND_TRAINING_LABEL_ONLY",
            "C14_ID": C14_ID,
            "C14_DECISION_FEATURE": False,
            "C14_FALLBACK": False,
            "CURRENT_EXIT": "C14_UNCHANGED",
            "SESSION": SESSION,
            "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
            "RUNTIME_WAIT_SEC": float(WAIT_SEC),
            "SYMBOL_FEATURES": list(SYMBOL_FEATURES),
            "REGIME_FEATURES": list(REGIME_FEATURES),
            "POSITION_FEATURES": list(POSITION_FEATURES),
            "EXIT_FEATURES": list(EXIT_FEATURES),
            "LOGREG_PARAMS": dict(LOGREG_PARAMS),
            "SCALER": "StandardScaler",
            "DECISION_START": "FIRST_VALID_CONTINUOUS_BOARD_EVENT_AFTER_FILL",
            "MIN_HOLD_SEC": None,
            "TRADE_WEIGHT": "1/VALID_STATE_N",
            "TARGET": "EXIT_NOW_PNL_YEN_100 > C14_FINAL_PNL_YEN_100",
            "OUTER_FOLD_N": int(OUTER_FOLD_N),
            "INNER_SEARCH": False,
            "FEATURE_SEARCH": False,
            "MODEL_SEARCH": False,
            "THRESHOLD_SEARCH": False,
            "RESEARCH_PARALLELISM": int(RESEARCH_PARALLELISM),
            "TRUE_OOS": False,
            "NEW_FORWARD_N": 0,
            "ENTRY_CHANGE": False,
            "PTL": False,
            "CONTINUATION_600_750": False,
            "TRAILING": False,
            "TP_SL": False,
        }
    )


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED", spec.get("ARCHITECTURE_ID"), sha, flush=True)
    print("TIME_FEATURE_N", spec.get("TIME_FEATURE_N"), "HOLDING_TIME_RULE_N", spec.get("HOLDING_TIME_RULE_N"), flush=True)

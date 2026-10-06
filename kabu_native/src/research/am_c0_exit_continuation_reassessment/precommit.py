"""Lock continuation branch-admission arms. No threshold search. No new 750 logic."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_c0_exit_continuation_reassessment import (
    ANALYSIS_ID,
    ARM_IDS,
    CONTINUATION_ARM_N,
    DECISION_SEC,
    E0,
    E1,
    E2,
    ENTRY_PARENT_ID,
    ENTRY_PARENT_SHA256,
    EXIT_SCOPE,
    EXTEND_SEC,
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
            "ENTRY_PARENT_ID": ENTRY_PARENT_ID,
            "ENTRY_PARENT_NAME": PROSPECTIVE_CHALLENGER_NAME,
            "ENTRY_PARENT_STATUS": PROSPECTIVE_STATUS,
            "ENTRY_PARENT_SHA256": ENTRY_PARENT_SHA256,
            "EXIT_SCOPE": EXIT_SCOPE,
            "CONTINUATION_ARM_N": int(CONTINUATION_ARM_N),
            "ARM_IDS": list(ARM_IDS),
            "E0": E0,
            "E1": E1,
            "E2": E2,
            "E1_RULE": "CONT_EXIT_600 AND EVER_PROFITABLE_PRE600 → existing CONT_EXTEND_750",
            "E2_RULE": "CONT_EXIT_600 → existing CONT_EXTEND_750",
            "EVER_PROFITABLE": "EXECUTABLE_UNREALIZED_PNL_YEN_100 > 0 before 600 exclusive",
            "DECISION_SEC": float(DECISION_SEC),
            "EXTEND_SEC": float(EXTEND_SEC),
            "NEW_750_LOGIC": False,
            "C14_ID": C14_ID,
            "C14_750_STATE": "CONT_EXTEND_750",
            "CURRENT_EXIT": "C14_UNCHANGED",
            "PTL_REUSE": False,
            "THRESHOLD_SEARCH": False,
            "HORIZON_SEARCH": False,
            "NEW_CONTINUATION_SIGNAL": False,
            "SESSION": SESSION,
            "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
            "RUNTIME_WAIT_SEC": float(WAIT_SEC),
            "TRUE_OOS": False,
            "NEW_FORWARD_N": 0,
        }
    )


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED", spec.get("ANALYSIS_ID"), sha, flush=True)
    print("ARMS", spec.get("E0"), spec.get("E1"), spec.get("E2"), flush=True)

"""Lock PTL guard architecture before any harvest/replay. No threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_c0_exit_ptl_guard import (
    ANALYSIS_ID,
    ARCHITECTURE_ID,
    C14_AFTER_600,
    EARLIEST_TRIGGER_SEC,
    ENTRY_PARENT_ID,
    ENTRY_PARENT_SHA256,
    EXIT_REASON,
    EXIT_SCOPE,
    LATEST_TRIGGER_SEC,
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
            "PROFIT_ARM": "EXECUTABLE_UNREALIZED_PNL > 0",
            "TRIGGER": "EXECUTABLE_UNREALIZED_PNL <= 0",
            "EARLIEST_TRIGGER_SEC": float(EARLIEST_TRIGGER_SEC),
            "LATEST_TRIGGER_SEC": float(LATEST_TRIGGER_SEC),
            "LATEST_TRIGGER_INCLUSIVE": False,
            "EXIT_REASON": EXIT_REASON,
            "C14_AFTER_600": C14_AFTER_600,
            "C14_ID": C14_ID,
            "CURRENT_EXIT": "C14_UNCHANGED",
            "SESSION": SESSION,
            "DEV_WAIT_SEC": float(DEV_WAIT_SEC),
            "RUNTIME_WAIT_SEC": float(WAIT_SEC),
            "THRESHOLD_SEARCH": False,
            "TRAILING_SEARCH": False,
            "MFE_ORACLE_EXIT": False,
            "L3_REPAIR": False,
            "EXTENSION_CHANGE": False,
            "ENTRY_CHANGE": False,
            "TRUE_OOS": False,
            "NEW_FORWARD_N": 0,
        }
    )


def spec_sha256(spec: dict[str, Any]) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def print_precommit(spec: dict[str, Any], sha: str) -> None:
    print("PRECOMMIT_LOCKED", spec.get("ARCHITECTURE_ID"), sha, flush=True)
    print(
        "EXIT_SCOPE",
        spec.get("EXIT_SCOPE"),
        "WINDOW",
        spec.get("EARLIEST_TRIGGER_SEC"),
        spec.get("LATEST_TRIGGER_SEC"),
        "exclusive",
        flush=True,
    )

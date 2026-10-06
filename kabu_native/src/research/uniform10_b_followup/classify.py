"""t0 executability class from ingest-time is_executable_continuous_board flags."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x34a_execution_policy.executable_board import (
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
)

ITAYOSE_STATES = {STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE}
SPECIAL_STATES = {STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD}


def classify_t0_row(src: dict[str, Any], *, event_t: Optional[float] = None) -> dict[str, Any]:
    del event_t
    ok = bool(src.get("executable"))
    state = str(src.get("board_execution_state") or "")
    locked = bool(src.get("locked_or_crossed"))
    if ok:
        return {
            "executable_at_t0": True,
            "class": "EXECUTABLE_CONTINUOUS_AT_ANCHOR",
            "nonexec_bucket": None,
            "state": state,
            "locked_or_crossed": locked,
        }
    if state in ITAYOSE_STATES:
        bucket = "ITAYOSE / NOT_OPENED"
    elif state in SPECIAL_STATES:
        bucket = "SPECIAL"
    elif locked:
        bucket = "LOCKED"
    else:
        bucket = "OTHER"
    return {
        "executable_at_t0": False,
        "class": "NON_EXECUTABLE_AT_ANCHOR",
        "nonexec_bucket": bucket,
        "state": state,
        "locked_or_crossed": locked,
    }

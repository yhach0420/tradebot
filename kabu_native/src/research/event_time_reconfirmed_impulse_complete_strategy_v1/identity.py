"""Identity of the new candidate. The parent signal document is not edited."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_obj(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def bind(signal_sha: str) -> dict[str, Any]:
    entry = {
        "ENTRY_EXECUTION_ID": "EVENT_TIME_FIRST_RECONFIRM_IMMEDIATE_ASK_V1",
        "entry_event": "first IMPULSE_RECONFIRM with PRE_BREAK_HIGH_NEW > watch support",
        "initial_full_is_entry": False,
        "entry_price": "ask of the reconfirm event",
        "wait": False,
        "chase": False,
    }
    exit_doc = {
        "hard_exit": "finite CurrentPrice <= ACTIVE_SUPPORT_LEVEL",
        "soft_exit": "participation lost AND activity lost AND 30 seconds without reconfirm",
        "priority": ["FAIL_CLOSE_INVALID_DATA", "BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT"],
        "execution": "first causal executable bid at or after the exit event",
    }
    entry_sha = sha256_obj(entry)
    exit_sha = sha256_obj(exit_doc)
    strategy = {
        "COMPLETE_STRATEGY_ID": "EVENT_TIME_RECONFIRMED_IMPULSE_COMPLETE_STRATEGY_V1",
        "signal_sha256": signal_sha,
        "execution_sha256": entry_sha,
        "exit_sha256": exit_sha,
        "max_concurrent": 5,
        "shares": 100,
    }
    return {
        "entry": entry,
        "entry_sha256": entry_sha,
        "exit": exit_doc,
        "exit_sha256": exit_sha,
        "strategy_sha256": sha256_obj(strategy),
    }

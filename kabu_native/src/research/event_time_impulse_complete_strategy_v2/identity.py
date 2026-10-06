"""Hashes for the new execution and exit. The parent signal document is not edited."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.event_time_impulse_complete_strategy_v2 import (
    COMPLETE_STRATEGY_ID,
    ENTRY_EXECUTION_ID,
    MAX_CONCURRENT,
    RECONFIRM_GAP_SEC,
    SHARES,
)
from research.event_time_volume_confirmed_impulse_entry.identity import identities as parent_identities


def sha256_obj(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def execution_document() -> dict[str, Any]:
    return {
        "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
        "family": "IMMEDIATE_ASK",
        "entry_price": "ask of the FULL signal event",
        "wait": False,
        "later_quote": False,
        "repricing": False,
        "chase": False,
        "passive_bid": False,
        "acceptance_window": False,
        "quote": ["bid>0", "ask>0", "ask>=bid", "bid_qty>=100", "ask_qty>=100", "fresh", "continuous", "non-special"],
        "unavailable": "ENTRY_UNAVAILABLE",
    }


def exit_document() -> dict[str, Any]:
    return {
        "support_initial": "ENTRY_PRE_BREAK_HIGH",
        "ratchet": "IMPULSE_RECONFIRM and PRE_BREAK_HIGH_NEW > ACTIVE_SUPPORT_LEVEL",
        "reconfirm_omits_spread": True,
        "hard_exit": "finite CurrentPrice <= ACTIVE_SUPPORT_LEVEL",
        "soft_exit": "BUY_PARTICIPATION_LOST AND ACTIVITY_LOST AND no reconfirm for 30 seconds",
        "reconfirm_gap_sec": RECONFIRM_GAP_SEC,
        "priority": ["FAIL_CLOSE_INVALID_DATA", "BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT"],
        "execution": "first causal executable bid at or after the exit event",
        "forbidden": ["EMA", "fixed_bps_stop", "fixed_bps_target", "percent_trail", "ATR", "RCI_only", "BB_only", "arbitrary_time_stop"],
    }


def strategy_document(signal_sha: str, execution_sha: str, exit_sha: str) -> dict[str, Any]:
    return {
        "COMPLETE_STRATEGY_ID": COMPLETE_STRATEGY_ID,
        "signal_sha256": signal_sha,
        "execution_sha256": execution_sha,
        "exit_sha256": exit_sha,
        "shares": SHARES,
        "max_concurrent": MAX_CONCURRENT,
        "same_symbol": True,
        "reentry": "new parent FULL episode after slot release",
    }


def bind() -> dict[str, Any]:
    parent = parent_identities()
    execution = execution_document()
    exit_doc = exit_document()
    execution_sha = sha256_obj(execution)
    exit_sha = sha256_obj(exit_doc)
    signal_sha = parent["ENTRY_SIGNAL_SHA256"]
    strategy = strategy_document(signal_sha, execution_sha, exit_sha)
    return {
        "signal_sha256": signal_sha,
        "parent_execution_sha256": parent["EXECUTION_SHA256"],
        "execution": execution,
        "execution_sha256": execution_sha,
        "exit": exit_doc,
        "exit_sha256": exit_sha,
        "strategy": strategy,
        "strategy_sha256": sha256_obj(strategy),
    }

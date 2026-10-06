"""Candidate identity. Distinct from frozen V2."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.event_time_impulse_complete_strategy_v2.identity import bind as bind_v2
from research.event_time_impulse_complete_strategy_v2.identity import sha256_obj
from research.event_time_impulse_fixed_entry_support_candidate_v1 import (
    CANDIDATE_ID,
    KNOWN_LIMITATION,
    MAX_CONCURRENT,
    PARENT_STRATEGY_ID,
    RECONFIRM_GAP_SEC,
    SHARES,
    STATUS,
    VALIDATION,
)
from research.event_time_volume_confirmed_impulse_entry.identity import identities as parent_identities

V2_STRATEGY_SHA = "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"


def entry_document() -> dict[str, Any]:
    return {
        "ENTRY_EXECUTION_ID": "EVENT_TIME_IMPULSE_IMMEDIATE_ASK_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "same_as": "frozen V2 immediate ask",
        "family": "IMMEDIATE_ASK",
        "entry_price": "ask of the FULL signal event",
        "wait": False,
        "later_quote": False,
        "repricing": False,
        "chase": False,
        "passive_bid": False,
    }


def exit_document() -> dict[str, Any]:
    return {
        "support_initial": "ENTRY_PRE_BREAK_HIGH",
        "active_support": "never moves after entry",
        "reconfirm_updates": ["last_reconfirm_time", "soft-exit clock", "reconfirm observer"],
        "reconfirm_does_not_update": ["ACTIVE_SUPPORT_LEVEL"],
        "hard_exit": "finite CurrentPrice <= ENTRY_PRE_BREAK_HIGH",
        "soft_exit": "BUY_PARTICIPATION_LOST AND ACTIVITY_LOST AND no reconfirm for 30 seconds",
        "reconfirm_gap_sec": RECONFIRM_GAP_SEC,
        "priority": ["FAIL_CLOSE_INVALID_DATA", "BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT"],
        "execution": "first causal executable bid at or after the exit event",
        "known_limitation": KNOWN_LIMITATION,
        "forbidden": ["support_buffer", "partial_ratchet", "symbol_whitelist", "new_timeout"],
    }


def strategy_document(signal_sha: str, entry_sha: str, exit_sha: str) -> dict[str, Any]:
    return {
        "COMPLETE_STRATEGY_ID": CANDIDATE_ID,
        "parent_strategy_id": PARENT_STRATEGY_ID,
        "status": STATUS,
        "validation": VALIDATION,
        "signal_sha256": signal_sha,
        "entry_sha256": entry_sha,
        "exit_sha256": exit_sha,
        "shares": SHARES,
        "max_concurrent": MAX_CONCURRENT,
        "same_symbol": True,
        "reentry": "new parent FULL episode after slot release",
        "universe": "FIXED_RESEARCH_OBSERVATION_UNIVERSE_105_V1",
        "dynamic40_applied": False,
        "known_limitation": KNOWN_LIMITATION,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def source_tree_sha256() -> str:
    root = Path(__file__).resolve().parent
    parts = []
    for path in sorted(root.glob("*.py")):
        parts.append(path.name.encode("utf-8") + b"\0" + path.read_bytes())
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def bind() -> dict[str, Any]:
    parent = parent_identities()
    entry = entry_document()
    exit_doc = exit_document()
    entry_sha = sha256_obj(entry)
    exit_sha = sha256_obj(exit_doc)
    signal_sha = parent["ENTRY_SIGNAL_SHA256"]
    strategy = strategy_document(signal_sha, entry_sha, exit_sha)
    strategy_sha = sha256_obj(strategy)
    if strategy_sha == V2_STRATEGY_SHA or strategy_sha == bind_v2()["strategy_sha256"]:
        raise RuntimeError("candidate_sha_collides_with_frozen_v2")
    manifest = {
        "candidate_id": CANDIDATE_ID,
        "entry_sha256": entry_sha,
        "exit_sha256": exit_sha,
        "complete_strategy_sha256": strategy_sha,
        "signal_sha256": signal_sha,
        "source_tree_sha256": source_tree_sha256(),
        "known_limitation": KNOWN_LIMITATION,
        "previous_baseline": PARENT_STRATEGY_ID,
        "status": STATUS,
    }
    manifest["manifest_sha256"] = sha256_obj(manifest)
    return {
        "entry": entry,
        "entry_sha256": entry_sha,
        "exit": exit_doc,
        "exit_sha256": exit_sha,
        "strategy": strategy,
        "complete_strategy_sha256": strategy_sha,
        "manifest": manifest,
        "manifest_sha256": manifest["manifest_sha256"],
        "signal_sha256": signal_sha,
    }

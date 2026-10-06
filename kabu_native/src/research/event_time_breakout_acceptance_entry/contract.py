"""Acceptance and immediate-ask identities. Hashed before outcomes are used."""
from __future__ import annotations

from typing import Any

from research.event_time_breakout_acceptance_entry import (
    ACCEPTANCE_SEC,
    ENTRY_EXECUTION_ID,
    ENTRY_ID,
    REQUIRED_SIGNAL_SHA256,
)
from research.event_time_volume_confirmed_impulse_entry.identity import identities as parent_identities
from research.event_time_volume_confirmed_impulse_entry.identity import sha256_obj


def acceptance_document() -> dict[str, Any]:
    return {
        "ACCEPTANCE_WINDOW_SEC": ACCEPTANCE_SEC,
        "duration_searched": False,
        "break_level": "admitted signal PRE_BREAK_HIGH, not recomputed during the window",
        "window": "(signal_t, signal_t + 5 seconds]",
        "FAILED_BACK_BELOW_BREAK": "any finite positive CurrentPrice <= break_level inside the window",
        "HELD_ABOVE_BREAK_5S": "the window is inside the session, a usable price path exists, and no price is <= break_level",
        "price_buffer_bps": 0,
        "tick_buffer": 0,
        "session_boundary": "T+5 after the session end is ACCEPTANCE_UNAVAILABLE, not a failure",
        "missing_path": "no finite positive CurrentPrice in the window is ACCEPTANCE_UNAVAILABLE",
        "confirm_clock": "first two-sided fresh executable quote at or after T+5",
        "confirm_bid_rule": "confirm_bid > break_level, strict",
        "no_later_quote_search": True,
        "BASE_SIGNAL_CHANGED": False,
        "BASE_SIGNAL_SHA256": REQUIRED_SIGNAL_SHA256,
    }


def execution_document() -> dict[str, Any]:
    return {
        "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
        "population": "BREAKOUT_ACCEPTED_5S",
        "entry_t": "confirm_t",
        "entry_price": "confirm_ask",
        "passive_bid": False,
        "repricing": False,
        "improvement": False,
        "waiting_after_confirm": False,
        "alternatives_searched": False,
        "forbidden": ["passive_bid", "bid_plus_tick", "mid", "ask_offset", "limit_chase", "W1", "W3", "W5", "W10", "best_price"],
    }


def contract_ids() -> dict[str, Any]:
    parent = parent_identities()
    acceptance = acceptance_document()
    execution = execution_document()
    acceptance_sha = sha256_obj(acceptance)
    execution_sha = sha256_obj(execution)
    combined = sha256_obj(
        {
            "ENTRY_ID": ENTRY_ID,
            "base_signal_sha256": parent["ENTRY_SIGNAL_SHA256"],
            "acceptance_sha256": acceptance_sha,
            "execution_sha256": execution_sha,
        }
    )
    return {
        "parent_signal_sha256": parent["ENTRY_SIGNAL_SHA256"],
        "signal_sha_matches_required": parent["ENTRY_SIGNAL_SHA256"] == REQUIRED_SIGNAL_SHA256,
        "acceptance": acceptance,
        "execution": execution,
        "ACCEPTANCE_SHA256": acceptance_sha,
        "EXECUTION_SHA256": execution_sha,
        "ENTRY_SHA256": combined,
    }

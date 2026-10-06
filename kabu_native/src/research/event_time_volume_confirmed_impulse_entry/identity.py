"""Frozen signal and execution identities. Hashed before outcomes are used."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.event_time_volume_confirmed_impulse.contract import RESET_IMPLEMENTATION
from research.event_time_volume_confirmed_impulse_entry import (
    ENTRY_EXECUTION_ID,
    ENTRY_ID,
    ENTRY_SIGNAL_ID,
    PARENT_ANALYSIS_ID,
    PARENT_VERDICT,
    WAIT_SEC,
)


def sha256_obj(body: Any) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def signal_document() -> dict[str, Any]:
    return {
        "ENTRY_SIGNAL_ID": ENTRY_SIGNAL_ID,
        "parent_analysis_id": PARENT_ANALYSIS_ID,
        "parent_verdict": PARENT_VERDICT,
        "SIGNAL_CHANGED": False,
        "THRESHOLD_SEARCHED": False,
        "predicate": [
            "VOL_ACCEL_10",
            "VOL_ACCEL_30",
            "BUY_DOMINANT_10",
            "classified_10s_volume > 0",
            "TICK_ACCEL_10",
            "PRICE_BREAK",
            "SPREAD_NOT_WORSE",
        ],
        "parent_semantics": RESET_IMPLEMENTATION,
        "preserved_fields": [
            "signal_t",
            "break_level",
            "trigger_price",
            "bid0",
            "ask0",
            "spread0",
            "volume_10s",
            "volume_30s",
            "ask_vol_10s",
            "bid_vol_10s",
            "tick_count_10s",
            "classified_volume_fraction",
        ],
    }


def execution_document() -> dict[str, Any]:
    return {
        "ENTRY_EXECUTION_ID": ENTRY_EXECUTION_ID,
        "family": "PASSIVE_BID_W5",
        "conceptual_parent": "SIMPLE_TECH_V1_PASSIVE_BID_W5",
        "decision_clock": "exact event-time impulse signal",
        "entry_limit": "bid0 of the FULL signal event",
        "fill_window": "(signal_t, signal_t + 5 seconds]",
        "wait_sec": WAIT_SEC,
        "session_bound": "search stops at the frozen session end and does not cross lunch or the close",
        "fill_conditions": [
            "continuous/executable",
            "not special",
            "fresh_sec <= 5",
            "bid > 0",
            "ask > 0",
            "ask >= bid",
            "bid_qty >= 100",
            "ask_qty >= 100",
            "ask <= entry_limit",
        ],
        "fill_price": "entry_limit",
        "price_improvement": False,
        "repricing": False,
        "offset": False,
        "immediate_ask_candidate": False,
        "alternatives_searched": False,
        "forbidden": ["W1", "W2", "W3", "W10", "W30", "bid_plus_tick", "mid", "ask", "reprice", "chase", "market", "best_fill", "pnl_selected"],
    }


def identities() -> dict[str, Any]:
    signal = signal_document()
    execution = execution_document()
    signal_sha = sha256_obj(signal)
    execution_sha = sha256_obj(execution)
    combined = sha256_obj({"ENTRY_ID": ENTRY_ID, "signal_sha256": signal_sha, "execution_sha256": execution_sha})
    return {
        "signal": signal,
        "execution": execution,
        "ENTRY_SIGNAL_SHA256": signal_sha,
        "EXECUTION_SHA256": execution_sha,
        "ENTRY_SHA256": combined,
    }

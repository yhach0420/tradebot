"""Complete Strategy freeze identity. Structure SHA. Not a PnL SHA."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    CAP,
    COST_MODEL_ID,
    FROZEN_ENTRY_IDENTITY,
    HISTORICAL_FILL_ID,
    LIVE_FILL_SOT_ID,
    LUNCH_POLICY,
    OPS_EXIT_ID,
    SESSION_FLAT,
    SHARES,
    TECHNICAL_EXIT_ID,
)
from research.pb1_v4_complete_strategy_build_and_economic_validation.spec import source_sha256


COMPLETE_STRATEGY_IDENTITY = "PB1_V4_COMPLETE_STRATEGY_FROZEN_V1"

STATE_MACHINE = (
    "FLAT",
    "ENTRY_ALLOWED",
    "FILL",
    "OPEN_POSITION",
    "EXIT_PENDING",
    "EXIT_EXECUTED",
    "SLOT_RELEASE",
    "FLAT_OR_REENTRY_ELIGIBLE",
)


def strategy_contract(*, machine_sha: str, source_inventory_sha: str) -> dict[str, Any]:
    return {
        "complete_strategy_identity": COMPLETE_STRATEGY_IDENTITY,
        "entry_identity": FROZEN_ENTRY_IDENTITY,
        "entry_machine_sha": machine_sha,
        "source_inventory_sha": source_inventory_sha,
        "execution": {
            "live_paper_sot": LIVE_FILL_SOT_ID,
            "historical_fill": HISTORICAL_FILL_ID,
            "RESEARCH_EXECUTION_APPROXIMATION": True,
            "mid_forbidden": True,
            "same_bar_forbidden": True,
            "future_quote_forbidden": True,
            "cost_model": COST_MODEL_ID,
            "X1_TAX_BPS": float(X1_TAX_BPS),
            "X1_TAX_ROLE": "research_friction_stress_not_observed_spread",
            "shares": int(SHARES),
        },
        "technical_exit": {
            "id": TECHNICAL_EXIT_ID,
            "rule": "THESIS_LOST then first causal next-open, lunch skipped, PM allowed",
            "parameter_search": False,
        },
        "ops_exit": {
            "id": OPS_EXIT_ID,
            "session_flat": SESSION_FLAT,
            "lunch_policy": LUNCH_POLICY,
            "not_thesis_death": True,
        },
        "portfolio": {
            "cap": int(CAP),
            "cap_source": "v1r_primary_runtime.POSITION_CAP",
            "same_symbol_prohibition": True,
            "occupancy_increments_at": "FILL",
            "slot_release_at": "EXIT_FILL",
            "reentry": "after_slot_release_if_frozen_v4_emits_and_thesis_still_permits",
            "reentry_pnl_filter": False,
            "winner_only_reentry": False,
            "loser_reentry_block": False,
            "event_priority": ["EXIT", "FILL", "ADMIT"],
        },
        "state_machine": list(STATE_MACHINE),
        "ENTRY_RETUNE_FORBIDDEN": True,
    }


def complete_strategy_sha256(*, machine_sha: str, source_inventory_sha: str) -> str:
    payload = {
        "contract": strategy_contract(machine_sha=machine_sha, source_inventory_sha=source_inventory_sha),
        "harness_source_sha256": source_sha256(),
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

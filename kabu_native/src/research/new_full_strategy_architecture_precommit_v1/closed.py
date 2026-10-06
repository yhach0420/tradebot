"""Closed-lineage mechanism cards. Identities only. No candidate ranking PnL."""
from __future__ import annotations

from typing import Any


def closed_lineage_cards() -> list[dict[str, Any]]:
    return [
        {
            "LINEAGE_ID": "SIMPLE_FULL",
            "DECISION_MECHANISM": "Per-name first-cross of a 1m price/volume/VWAP conjunction (E1-E5) x execution x Z1-Z5 grid.",
            "ENTRY_ROLE": "Standing 1m bar predicate becomes true after being false.",
            "EXIT_ROLE": "Independent Z-grid, not jointly tied to one ENTRY thesis.",
        },
        {
            "LINEAGE_ID": "E4",
            "DECISION_MECHANISM": "VWAP reclaim first-cross (Close crosses above session VWAP).",
            "ENTRY_ROLE": "VWAP location reclaim.",
            "EXIT_ROLE": "Paired historically with X2/Z3; closed as symbol-dependent.",
        },
        {
            "LINEAGE_ID": "SYSTEMATIC_STATE_TRANSITION",
            "DECISION_MECHANISM": "Per-name PERSIST_NEXT (state still true one extra completed 1m bar) or HANDOFF_NEXT (state A then different state B next bar) on five frozen 1m states.",
            "ENTRY_ROLE": "Temporal occupancy / two-state handoff of MA, BB-mid, RCI>-80, volume confirm, Close>VWAP.",
            "EXIT_ROLE": "Universal Z3 two-bar weakness + session close.",
        },
        {
            "LINEAGE_ID": "C1_MULTI_TIMEFRAME",
            "DECISION_MECHANISM": "Same-name 1m state plus last completed 3m/5m bar of the SAME predicate as confirmation.",
            "ENTRY_ROLE": "Higher-timeframe completed-bar confirmation of a 1m technical state.",
            "EXIT_ROLE": "Paired Z1-Z5 library on the 1m clock.",
        },
        {
            "LINEAGE_ID": "C4_PORTFOLIO_CROWDING",
            "DECISION_MECHANISM": "Admission overlay: first unique candidate timestamp per ENTRY identity; CAP remains occupancy.",
            "ENTRY_ROLE": "Collision handling among already-defined ST/C1-style signals.",
            "EXIT_ROLE": "Inherited from the matched ENTRY/EXIT pair.",
        },
        {
            "LINEAGE_ID": "RECOVERY_SEQUENCE",
            "DECISION_MECHANISM": "Next-bar reclaim / acceptance / participation-confirm after a prior broken location.",
            "ENTRY_ROLE": "Path reclaim of a lost level.",
            "EXIT_ROLE": "Z3.",
        },
        {
            "LINEAGE_ID": "PARTICIPATION_ONSET",
            "DECISION_MECHANISM": "Per-name T1 activity-onset using frozen universe volume percentile.",
            "ENTRY_ROLE": "Own-name activity start, not a technical MA/BB/RCI state machine.",
            "EXIT_ROLE": "Z3 / participation-loss / hybrid.",
        },
        {
            "LINEAGE_ID": "PFQ",
            "DECISION_MECHANISM": "Closed E1_X7 PFQ research program. Not a live Full Strategy unit.",
            "ENTRY_ROLE": "PFQ overlay / quality.",
            "EXIT_ROLE": "Program-closed.",
        },
        {
            "LINEAGE_ID": "OR",
            "DECISION_MECHANISM": "Production open-strength overlay after PBv2 reject; rank/high/VWAP nearness.",
            "ENTRY_ROLE": "Fallback overlay, not a standalone research Full Strategy.",
            "EXIT_ROLE": "Shared production structural exit.",
        },
        {
            "LINEAGE_ID": "DYNAMIC_ANCHOR",
            "DECISION_MECHANISM": "Fill-time or trailing anchor as EXIT/hold geometry.",
            "ENTRY_ROLE": "Not a new ENTRY class; closed EXIT/hold lineage.",
            "EXIT_ROLE": "Anchor / trail.",
        },
        {
            "LINEAGE_ID": "X9",
            "DECISION_MECHANISM": "Universe-regime / institutional-participation audit of the PFQ line, not a new ENTRY/EXIT state machine.",
            "ENTRY_ROLE": "Regime split of a closed program.",
            "EXIT_ROLE": "Program-closed.",
        },
    ]


def revival_flags() -> dict[str, bool]:
    return {
        "REVIVE_SIMPLE_FULL": False,
        "REVIVE_E4": False,
        "REVIVE_ST": False,
        "REVIVE_C1": False,
        "REVIVE_C4": False,
        "REVIVE_RECOVERY": False,
        "REVIVE_PARTICIPATION": False,
        "REVIVE_PFQ": False,
        "REVIVE_OR": False,
        "REVIVE_DYNAMIC_ANCHOR": False,
        "REVIVE_X9": False,
    }

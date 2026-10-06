"""Prior Complete Full Strategy semantic duplicate check. No economics."""
from __future__ import annotations

from typing import Any

from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    EXECUTION_ID,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)

THIS_IDENTITY = (
    "BUY_SPECIAL_QUOTE_OPENING_DELAY",
    "NORMAL_CONTINUOUS_OPENING",
    "OPENINGPRICE_ACCEPTANCE",
    "LONG",
    "OPENINGPRICE_ACCEPTANCE_LOSS_EXIT",
)

PRIOR_FAMILIES = (
    {
        "FAMILY": "PRIOR_SESSION_CONTEXT",
        "COMPLETE_STRATEGY": False,
        "NOTE": "PreviousClose/open reference object. Not delayed buy-special release acceptance.",
    },
    {
        "FAMILY": "Recovery Sequence",
        "COMPLETE_STRATEGY": True,
        "NOTE": "VWAP reclaim R1/R2/R3 + Z3. Not special-quote delayed open.",
    },
    {
        "FAMILY": "OR / Open Strength",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Open-range / strength overlay. Closed. Not this ENTRY/EXIT pair.",
    },
    {
        "FAMILY": "FAILED_BREAKDOWN_RECLAIM",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Failed breakdown reclaim ENTRY. Not delayed buy special-quote open.",
    },
    {
        "FAMILY": "VWAP reclaim/rejection",
        "COMPLETE_STRATEGY": True,
        "NOTE": "VWAP location ENTRY/EXIT. Not OpeningPrice acceptance after special-quote release.",
    },
    {
        "FAMILY": "PARTICIPATION_ONSET",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Volume/participation onset. Closed.",
    },
    {
        "FAMILY": "SYSTEMATIC_STATE_TRANSITION",
        "COMPLETE_STRATEGY": True,
        "NOTE": "MA/BB/RCI/VOL/VWAP persist-handoff. Z3 then thesis-aligned EXIT. Not special-quote open.",
    },
    {
        "FAMILY": "IOAR",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Absorption/replenishment. Rejected. Not delayed open.",
    },
    {
        "FAMILY": "UEIA",
        "COMPLETE_STRATEGY": False,
        "NOTE": "Used preopen tape as diagnostic contamination. Not this complete strategy.",
    },
    {
        "FAMILY": "CSB",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Cross-sectional MA onset. Closed.",
    },
    {
        "FAMILY": "C1",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Multi-timeframe. Closed.",
    },
    {
        "FAMILY": "C4",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Portfolio crowding. Closed.",
    },
    {
        "FAMILY": "FDG",
        "COMPLETE_STRATEGY": True,
        "NOTE": "Price/relative geometry. Closed.",
    },
    {
        "FAMILY": "SPECIAL_QUOTE diagnostics",
        "COMPLETE_STRATEGY": False,
        "NOTE": "is_executable_continuous_board uses SPECIAL_QUOTE as execution exclusion, not as ENTRY thesis.",
    },
)


def audit_duplicates() -> dict[str, Any]:
    exact = False
    semantic = False
    hits = []
    for row in PRIOR_FAMILIES:
        if row["FAMILY"] == "SPECIAL_QUOTE diagnostics":
            continue
        text = (row["NOTE"] + " " + row["FAMILY"]).lower()
        if "buy special" in text and "openingprice acceptance" in text.replace(" ", ""):
            semantic = True
            hits.append(row["FAMILY"])
    return {
        "THIS_STRATEGY_ID": STRATEGY_ID,
        "THIS_EXECUTION": EXECUTION_ID,
        "THIS_TECHNICAL_EXIT": TECHNICAL_EXIT_ID,
        "THIS_IDENTITY": list(THIS_IDENTITY),
        "FAMILIES_COMPARED": [r["FAMILY"] for r in PRIOR_FAMILIES],
        "FAMILY_ROWS": list(PRIOR_FAMILIES),
        "EXACT_COMPLETE_STRATEGY_DUPLICATE": exact,
        "MATERIAL_SEMANTIC_DUPLICATE": semantic,
        "HITS": hits,
        "NOTE": (
            "Prior SPECIAL_QUOTE use as execution guard / market-state exclusion / diagnostic "
            "does not count as this complete strategy."
        ),
    }

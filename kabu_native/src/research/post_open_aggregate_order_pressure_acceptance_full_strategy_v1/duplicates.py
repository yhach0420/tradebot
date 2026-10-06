"""Duplicate check vs IOAR/FDG/OPENING_AUCTION. Names do not decide; fields+predicate+EXIT do."""
from __future__ import annotations

from typing import Any

from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    EXECUTION_ID,
    EXIT_ID,
    PRESSURE_FIELDS,
    STRATEGY_ID,
)

THIS_IDENTITY = {
    "RAW_FIELDS": list(PRESSURE_FIELDS),
    "DECISION_PREDICATE": "MarketOrderBuyQty > MarketOrderSellQty AND UnderBuyQty > OverSellQty (strict, no ratio)",
    "ANCHOR": "last Observed Trade Update CurrentPrice strictly before PRESSURE_START",
    "ENTRY": "first post-start Observed Trade Update with CurrentPrice > PRESSURE_ANCHOR while BUY_PRESSURE still true",
    "EXIT": "first post-fill Observed Trade Update with NOT BUY_PRESSURE AND CurrentPrice < PRESSURE_ANCHOR",
}

PRIOR = (
    {
        "FAMILY": "IOAR",
        "RAW_FIELDS": ["derived trade_side", "Buy1", "Sell1", "absorption/replenishment"],
        "PREDICATE": "IOAR S1-S5 absorption/replenishment on L1",
        "ANCHOR": "IOAR state machine levels",
        "ENTRY": "IOAR exact mechanism",
        "EXIT": "IOAR family EXIT",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Uses derived trade_side and Buy1/Sell1, not the four aggregate qty keys.",
    },
    {
        "FAMILY": "UEIA",
        "RAW_FIELDS": ["preopen tape / market context"],
        "PREDICATE": "UEIA sampling",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Preopen contamination / market-context. Not post-open dual aggregate pressure.",
    },
    {
        "FAMILY": "FULL_DEPTH_GEOMETRY",
        "RAW_FIELDS": ["Buy1..10", "Sell1..10"],
        "PREDICATE": "10-level ladder geometry",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "This strategy does not use 10-level geometry.",
    },
    {
        "FAMILY": "FDG_RELATIVE_MIGRATION",
        "RAW_FIELDS": ["Buy1..10", "Sell1..10"],
        "PREDICATE": "relative ladder migration",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "10-level relative geometry, not MarketOrder*/Over/Under qty.",
    },
    {
        "FAMILY": "EVENT_FLOW",
        "RAW_FIELDS": ["snapshot deltas"],
        "PREDICATE": "quote-update event flow",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Generic event flow, not dual aggregate buy-pressure predicate.",
    },
    {
        "FAMILY": "TRADE_FLOW",
        "RAW_FIELDS": ["TradingVolume", "CurrentPrice deltas"],
        "PREDICATE": "print/aggressor flow",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Observed trades are confirmation only; ENTRY predicate is the four qty fields.",
    },
    {
        "FAMILY": "PARTICIPATION_ONSET",
        "RAW_FIELDS": ["volume participation"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "SYSTEMATIC_STATE_TRANSITION",
        "RAW_FIELDS": ["MA/BB/RCI/VOL/VWAP"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "FCMD_42",
        "RAW_FIELDS": ["O1/O2/O3 state operators"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "C1",
        "RAW_FIELDS": ["multi-timeframe"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "C4",
        "RAW_FIELDS": ["portfolio crowding"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "CSB",
        "RAW_FIELDS": ["cross-sectional MA onset"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "OPENING_AUCTION_CONTEXT",
        "RAW_FIELDS": list(PRESSURE_FIELDS),
        "PREDICATE": "pre-09:00 freeze of MO/OVER/UNDER as auction context",
        "ANCHOR": "preopen imbalance freeze",
        "ENTRY": "not a Complete Full Strategy; PREOPEN_EXECUTION_VALID=false",
        "EXIT": None,
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": (
            "Same four keys were used as pre-open context freeze. This strategy uses only "
            "post-open continuous dynamic payload values plus Observed Trade Update confirmation. "
            "Removing the post-open dynamic PRESSURE_START would collapse it to the closed auction freeze."
        ),
    },
)


def audit_duplicates() -> dict[str, Any]:
    exact = any(bool(r.get("EXACT")) for r in PRIOR)
    material = any(bool(r.get("MATERIAL")) for r in PRIOR)
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "EXECUTION": EXECUTION_ID,
        "EXIT_ID": EXIT_ID,
        "THIS_IDENTITY": THIS_IDENTITY,
        "FAMILIES_COMPARED": [r["FAMILY"] for r in PRIOR],
        "FAMILY_ROWS": list(PRIOR),
        "EXACT_DUPLICATE": exact,
        "MATERIAL_SEMANTIC_DUPLICATE": material,
        "NOTE": "Compared raw fields, predicate, anchor, ENTRY, EXIT. Same keys in OPENING_AUCTION_CONTEXT are a preopen freeze, not this post-open dynamic strategy.",
    }

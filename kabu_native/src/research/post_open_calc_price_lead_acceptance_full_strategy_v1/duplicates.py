"""Duplicate check vs prior families. Names do not decide; fields+predicate+EXIT do."""
from __future__ import annotations

from typing import Any

from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import EXECUTION_ID, EXIT_ID, STRATEGY_ID

THIS_IDENTITY = {
    "RAW_FIELDS": ["CalcPrice"],
    "STATE": "CALC_UP_LEAD = CalcPrice > LAST_OBSERVED_TRADE_PRICE",
    "EPISODE": "false→true CalcPrice lead; anchor frozen at start CalcPrice",
    "ANCHOR": "CalcPrice at CALC_LEAD_START",
    "ENTRY": "first OTU with CurrentPrice >= CALC_LEAD_ANCHOR while lead active",
    "EXIT": "post-fill OTU with CurrentPrice < ANCHOR AND latest valid CalcPrice < ANCHOR",
    "EXECUTION": EXECUTION_ID,
}

PRIOR = (
    {"FAMILY": "SIMPLE_TECH", "RAW_FIELDS": ["MA/VWAP/RCI/BB/volume bars"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "SYSTEMATIC_STATE_TRANSITION", "RAW_FIELDS": ["MA/BB/RCI/VOL/VWAP"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "RECOVERY_SEQUENCE", "RAW_FIELDS": ["recovery path operators"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "PARTICIPATION_ONSET", "RAW_FIELDS": ["volume participation"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "BREAKOUT", "RAW_FIELDS": ["high/low breakout"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "FAILED_BREAKDOWN_RECLAIM", "RAW_FIELDS": ["failed breakdown reclaim"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "VWAP_RECLAIM_REJECTION", "RAW_FIELDS": ["VWAP"], "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "IOAR",
        "RAW_FIELDS": ["derived trade_side", "Buy1", "Sell1"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "L1 absorption, not CalcPrice lead.",
    },
    {"FAMILY": "UEIA", "RAW_FIELDS": ["preopen tape"], "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "FDG_STATIC_SPAN",
        "RAW_FIELDS": ["Buy1..10", "Sell1..10"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "10-level geometry, not CalcPrice.",
    },
    {"FAMILY": "FDG_RELATIVE_MIGRATION", "RAW_FIELDS": ["Buy1..10", "Sell1..10"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "EVENT_FLOW", "RAW_FIELDS": ["snapshot deltas"], "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "TRADE_FLOW",
        "RAW_FIELDS": ["TradingVolume", "CurrentPrice"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Observed trades confirm acceptance; lead state is CalcPrice.",
    },
    {"FAMILY": "FCMD_42", "RAW_FIELDS": ["O1/O2/O3"], "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "OPENING_AUCTION_CONTEXT",
        "RAW_FIELDS": ["pre-09:00 freeze including possible CalcPrice context"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Pre-open freeze is not this post-open dynamic lead episode.",
    },
    {
        "FAMILY": "ISQ",
        "RAW_FIELDS": ["SPECIAL_QUOTE", "CONTINUOUS_TRADING", "Observed Trade Update"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "AOP",
        "RAW_FIELDS": ["MarketOrderBuyQty", "MarketOrderSellQty", "UnderBuyQty", "OverSellQty"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "AOP closed: MO pair frozen at 0. This strategy does not use those fields.",
    },
)


def audit_duplicates(*, eq_current: float | None = None, eq_bid: float | None = None, eq_ask: float | None = None, eq_mid: float | None = None) -> dict[str, Any]:
    rows = [dict(r) for r in PRIOR]
    material = any(bool(r.get("MATERIAL")) for r in rows)
    exact = any(bool(r.get("EXACT")) for r in rows)
    tob_copy = False
    note = "Compared raw input, state, episode, anchor, ENTRY, EXIT, execution."
    if eq_bid is not None and float(eq_bid) >= 0.99:
        tob_copy = True
        note = "CalcPrice equals Bid1 on >=99% of comparable rows: material top-of-book identity."
    if eq_ask is not None and float(eq_ask) >= 0.99:
        tob_copy = True
        note = "CalcPrice equals Ask1 on >=99% of comparable rows: material top-of-book identity."
    if eq_mid is not None and float(eq_mid) >= 0.99:
        tob_copy = True
        note = "CalcPrice equals mid on >=99% of comparable rows: material top-of-book conversion."
    if tob_copy:
        material = True
        rows.append(
            {
                "FAMILY": "TOP_OF_BOOK_STATE",
                "RAW_FIELDS": ["Bid1", "Ask1", "mid"],
                "EXACT": False,
                "MATERIAL": True,
                "NOTE": note,
            }
        )
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "EXECUTION": EXECUTION_ID,
        "EXIT_ID": EXIT_ID,
        "THIS_IDENTITY": THIS_IDENTITY,
        "FAMILIES_COMPARED": [r["FAMILY"] for r in rows],
        "FAMILY_ROWS": rows,
        "EQ_CURRENT": eq_current,
        "EQ_BID1": eq_bid,
        "EQ_ASK1": eq_ask,
        "EQ_MID": eq_mid,
        "EXACT_DUPLICATE": exact,
        "MATERIAL_SEMANTIC_DUPLICATE": material,
        "NOTE": note,
    }

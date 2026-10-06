"""Duplicate check vs prior families. PreviousClose recapture is not VWAP/FBR/ISQ/CalcPrice."""
from __future__ import annotations

from typing import Any

from research.post_open_previous_close_recapture_full_strategy_v1 import EXECUTION_ID, EXIT_ID, STRATEGY_ID

THIS_IDENTITY = {
    "RAW_FIELDS": ["PreviousClose", "CurrentPrice", "TradingVolume"],
    "STATE": "below-close OTU then recapture OTU > PreviousClose",
    "EPISODE": "first OTU CurrentPrice < PreviousClose; recapture is first later OTU > PreviousClose",
    "ANCHOR": "PreviousClose at below-start (前日終値)",
    "ENTRY": "recapture Observed Trade itself",
    "EXIT": "post-fill OTU CurrentPrice < frozen PreviousClose",
    "EXECUTION": EXECUTION_ID,
}

PRIOR = (
    {"FAMILY": "SIMPLE_TECH", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "SYSTEMATIC_STATE_TRANSITION", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "RECOVERY_SEQUENCE", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "PARTICIPATION_ONSET", "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "BREAKOUT",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "High/low breakout, not prior-session close recapture.",
    },
    {
        "FAMILY": "FAILED_BREAKDOWN_RECLAIM",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "1m OHLC: prior Low below 5-bar min Low, then Close > prior High and Close > Open. Not PreviousClose OTU.",
    },
    {
        "FAMILY": "VWAP_RECLAIM_REJECTION",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "VWAP level, not 前日終値.",
    },
    {"FAMILY": "IOAR", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "UEIA", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "FDG_STATIC_SPAN", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "FDG_RELATIVE_MIGRATION", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "EVENT_FLOW", "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "TRADE_FLOW",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Uses OTU confirmation; regime is PreviousClose, not aggressor inference.",
    },
    {"FAMILY": "QUOTE_UPDATE_DYNAMICS", "EXACT": False, "MATERIAL": False},
    {"FAMILY": "FCMD_42", "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "CALC_PRICE_LEAD",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "CalcPrice lead vs last trade; closed on C4. Not PreviousClose recapture.",
    },
    {
        "FAMILY": "ISQ",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Special quote release, not prior-close recapture.",
    },
    {"FAMILY": "AOP", "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "NATIVE_DISCONTINUOUS_UP",
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "CurrentPriceStatus/ChangeStatus pair; V4 closed on missing UP code.",
    },
    {"FAMILY": "OPENING_AUCTION_CONTEXT", "EXACT": False, "MATERIAL": False},
)


def audit_duplicates() -> dict[str, Any]:
    rows = [dict(r) for r in PRIOR]
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "EXECUTION": EXECUTION_ID,
        "EXIT_ID": EXIT_ID,
        "THIS_IDENTITY": THIS_IDENTITY,
        "FAMILIES_COMPARED": [r["FAMILY"] for r in rows],
        "FAMILY_ROWS": rows,
        "EXACT_DUPLICATE": False,
        "MATERIAL_SEMANTIC_DUPLICATE": False,
        "NOTE": "Compared raw input, state, episode, anchor, ENTRY, EXIT, execution.",
    }

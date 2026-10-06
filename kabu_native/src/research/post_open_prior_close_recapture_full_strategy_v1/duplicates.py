"""Duplicate check vs reclaim/VWAP/FBR/ISQ/CalcPrice. Names do not decide."""
from __future__ import annotations

from typing import Any

from research.post_open_prior_close_recapture_full_strategy_v1 import EXECUTION_ID, EXIT_ID, STRATEGY_ID

THIS_IDENTITY = {
    "RAW_FIELDS": ["PreviousClose", "Observed Trade Update CurrentPrice"],
    "STATE": "BELOW_REGIME then RECAPTURE: OTU < PreviousClose, then OTU > PreviousClose",
    "EPISODE": "first below OTU starts wait; recapture OTU is ENTRY; one signal",
    "ANCHOR": "frozen AM PreviousClose",
    "ENTRY": "recapture OTU itself",
    "EXIT": "post-fill OTU CurrentPrice < PreviousClose",
    "EXECUTION": EXECUTION_ID,
}

PRIOR = (
    {
        "FAMILY": "VWAP_RECLAIM_REJECTION",
        "RAW_FIELDS": ["VWAP"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Session VWAP location, not prior-session close. Different raw object.",
    },
    {
        "FAMILY": "FAILED_BREAKDOWN_RECLAIM",
        "RAW_FIELDS": ["1m OHLC 5-bar min Low then Close > prior High"],
        "EXACT": False,
        "MATERIAL": False,
    },
    {
        "FAMILY": "RECOVERY_SEQUENCE",
        "RAW_FIELDS": ["1m next-bar reclaim operators R1/R2/R3", "VWAP/structure"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "1m bar followthrough. Not OTU vs PreviousClose.",
    },
    {
        "FAMILY": "GAP_FILL_TO_PRIOR_CLOSE (inventory considered, never built)",
        "RAW_FIELDS": ["overnight gap size", "later bar fill"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Excluded as threshold-search bar gap-fill. This strategy has no gap-size and uses OTU regime then recapture.",
    },
    {"FAMILY": "BREAKOUT", "RAW_FIELDS": ["high/low breakout"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "PARTICIPATION_ONSET", "RAW_FIELDS": ["volume participation"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "TRADE_FLOW", "RAW_FIELDS": ["TradingVolume", "CurrentPrice"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "EVENT_FLOW", "RAW_FIELDS": ["snapshot deltas"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "QUOTE_UPDATE_DYNAMICS", "RAW_FIELDS": ["quote burst"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "IOAR", "RAW_FIELDS": ["L1 absorption"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "UEIA", "RAW_FIELDS": ["preopen tape"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "SYSTEMATIC_STATE_TRANSITION", "RAW_FIELDS": ["MA/BB/RCI"], "EXACT": False, "MATERIAL": False},
    {
        "FAMILY": "CALC_PRICE_LEAD",
        "RAW_FIELDS": ["CalcPrice"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "CalcPrice lead acceptance. CLOSED. Not PreviousClose.",
    },
    {
        "FAMILY": "ISQ",
        "RAW_FIELDS": ["SPECIAL_QUOTE", "release"],
        "EXACT": False,
        "MATERIAL": False,
        "NOTE": "Special quote episode is not required. Continuous only.",
    },
    {"FAMILY": "OPENING_AUCTION_CONTEXT", "RAW_FIELDS": ["pre-09:00 PreviousClose freeze"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "NATIVE_DISCONT_UP", "RAW_FIELDS": ["CurrentPriceStatus"], "EXACT": False, "MATERIAL": False},
    {"FAMILY": "AOP", "RAW_FIELDS": ["MarketOrderBuyQty"], "EXACT": False, "MATERIAL": False},
)


def audit_duplicates(*, special_quote_overlap_rate: float | None = None) -> dict[str, Any]:
    rows = [dict(r) for r in PRIOR]
    material = any(bool(r.get("MATERIAL")) for r in rows)
    exact = any(bool(r.get("EXACT")) for r in rows)
    note = "Compared raw input, state, episode, anchor, ENTRY, EXIT, execution."
    if special_quote_overlap_rate is not None and float(special_quote_overlap_rate) >= 0.99:
        material = True
        note = "Recapture events are substantially special-quote internals. Material ISQ duplicate."
        rows.append({"FAMILY": "ISQ_OVERLAP", "EXACT": False, "MATERIAL": True, "NOTE": note})
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "EXECUTION": EXECUTION_ID,
        "EXIT_ID": EXIT_ID,
        "THIS_IDENTITY": THIS_IDENTITY,
        "FAMILIES_COMPARED": [r["FAMILY"] for r in rows],
        "FAMILY_ROWS": rows,
        "SPECIAL_QUOTE_OVERLAP_RATE": special_quote_overlap_rate,
        "EXACT_DUPLICATE": exact,
        "MATERIAL_SEMANTIC_DUPLICATE": material,
        "NOTE": note,
    }

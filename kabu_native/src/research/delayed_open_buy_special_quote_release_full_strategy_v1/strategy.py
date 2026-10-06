"""Intended one-strategy freeze. SHA only after BUY direction is proven."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    BOARD_FRESHNESS_SEC,
    EXECUTION_ID,
    MAX_FILLED_ENTRY_PER_SYMBOL_SESSION,
    POSITION_CAP,
    SESSION_FLATTEN_HM,
    SHARES_N,
    STRATEGY_ID,
    TECHNICAL_EXIT_ID,
)


def strategy_spec() -> dict[str, Any]:
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "SIDE": "LONG_ONLY",
        "ENTRY": (
            "DELAYED_BUY_SPECIAL_OPEN_EPISODE after 09:00 with no valid OpeningPrice; "
            "first BUY_SIDE_SPECIAL_QUOTE; then RELEASE when OpeningPrice+OpeningPriceTime valid "
            "and trusted classifier is CONTINUOUS_TRADING; ACCEPT_BAR = first completed 1m bar "
            "starting strictly after the minute of RELEASE_TIME; ENTRY iff ACCEPT_BAR.Close > RELEASE_PRICE."
        ),
        "EXECUTION": EXECUTION_ID,
        "SHARES": SHARES_N,
        "TECHNICAL_EXIT": TECHNICAL_EXIT_ID,
        "TECHNICAL_EXIT_RULE": "first later completed 1m bar Close < RELEASE_PRICE → EXIT_PENDING → first causal Bid1",
        "CAP": int(POSITION_CAP),
        "MAX_FILLED_ENTRY_PER_SYMBOL_SESSION": int(MAX_FILLED_ENTRY_PER_SYMBOL_SESSION),
        "SESSION_CLOSE": f"inherited AM flatten {SESSION_FLATTEN_HM}",
        "BOARD_FRESHNESS_SEC": float(BOARD_FRESHNESS_SEC),
        "FORBIDDEN": [
            "volume filter",
            "board imbalance threshold",
            "gap threshold",
            "PreviousClose condition",
            "RCI/MA/BB/VWAP",
            "Low>=RELEASE_PRICE",
            "special-quote duration threshold",
        ],
    }


def freeze_sha(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec if spec is not None else strategy_spec())

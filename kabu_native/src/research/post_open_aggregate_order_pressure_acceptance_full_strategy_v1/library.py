"""Frozen one-candidate library. SHA before counts and economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    EXECUTION_ID,
    EXIT_ID,
    POSITION_CAP_N,
    PRESSURE_FIELDS,
    SESSION_FLATTEN_HM,
    SHARES_N,
    STRATEGY_ID,
)


def strategy_spec() -> dict[str, Any]:
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "CANDIDATE_N": 1,
        "FIELDS": list(PRESSURE_FIELDS),
        "BUY_PRESSURE": (
            "all four finite and >=0 AND MarketOrderBuyQty > MarketOrderSellQty "
            "AND UnderBuyQty > OverSellQty; equality is false; NaN/negative is 未成立"
        ),
        "PRESSURE_START": "first causal false/未成立 → true; no new start while still true",
        "PRESSURE_ANCHOR": "last Observed Trade Update CurrentPrice strictly before PRESSURE_START",
        "ENTRY": "first Observed Trade Update after start with BUY_PRESSURE true and CurrentPrice > ANCHOR",
        "ENTRY_TIME": "that Observed Trade Update INGRESS",
        "EXECUTION": EXECUTION_ID,
        "SHARES": int(SHARES_N),
        "CAP": int(POSITION_CAP_N),
        "TECHNICAL_EXIT": EXIT_ID,
        "EXIT_FIRE": "first post-fill Observed Trade Update with NOT BUY_PRESSURE AND CurrentPrice < ANCHOR",
        "EXIT_FILL": "canonical first causal Bid1; no synthetic Bid",
        "SAME_RUN_REENTRY": False,
        "NEW_RUN_REENTRY": True,
        "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
        "SPECIAL_QUOTE_ENTRY": False,
        "PREOPEN_ENTRY": False,
        "FORBIDDEN": [
            "ratio",
            "bps/tick/ATR",
            "VWAP/MA/BB/RCI",
            "volume threshold",
            "duration",
            "second confirmation",
            "wait window",
            "holding-time EXIT",
            "universal EXIT",
            "field subset",
        ],
    }


def freeze_library_sha(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec if spec is not None else strategy_spec())


def freeze_strategy_sha(library_sha: str) -> str:
    return dumps_sha256({"STRATEGY_ID": STRATEGY_ID, "LIBRARY_SHA": library_sha, "SPEC": strategy_spec()})

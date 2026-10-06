"""Frozen one-candidate library. SHA before counts and economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import (
    EXECUTION_ID,
    EXIT_ID,
    POSITION_CAP_N,
    SESSION_FLATTEN_HM,
    SHARES_N,
    STRATEGY_ID,
)


def strategy_spec() -> dict[str, Any]:
    return {
        "STRATEGY_ID": STRATEGY_ID,
        "CANDIDATE_N": 1,
        "RAW_FIELD": "CalcPrice",
        "CONFIRMATION": "Observed Trade Update (TradingVolume increase AND CurrentPrice finite > 0)",
        "CALC_UP_LEAD": "valid CalcPrice > LAST_OBSERVED_TRADE_PRICE (strict >); continuous only; prior trade required",
        "EPISODE_START": "first false→true CALC_UP_LEAD; anchor = CalcPrice at start; immutable",
        "PREENTRY_FAIL": "latest CalcPrice <= LAST_OBSERVED_TRADE_PRICE before signal",
        "ENTRY": "first OTU while lead active with CurrentPrice >= CALC_LEAD_ANCHOR",
        "ENTRY_TIME": "that Observed Trade Update INGRESS",
        "EXECUTION": EXECUTION_ID,
        "SHARES": int(SHARES_N),
        "CAP": int(POSITION_CAP_N),
        "TECHNICAL_EXIT": EXIT_ID,
        "EXIT_FIRE": "post-fill OTU with CurrentPrice < ANCHOR AND latest valid CalcPrice < ANCHOR",
        "EXIT_FILL": "canonical first causal Bid1; no synthetic Bid",
        "SAME_EPISODE_REENTRY": False,
        "NEW_EPISODE_REENTRY": True,
        "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
        "SPECIAL_QUOTE_ENTRY": False,
        "PREOPEN_ENTRY": False,
        "FORBIDDEN": [
            "bps/tick/ATR",
            "ratio/z-score",
            "MA/VWAP/RCI/BB",
            "volume threshold",
            "duration",
            "holding-time EXIT",
            "universal EXIT",
            "CurrentPriceStatus",
            "Under/Over",
            "board imbalance",
        ],
    }


def freeze_library_sha(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec if spec is not None else strategy_spec())


def freeze_strategy_sha(library_sha: str) -> str:
    return dumps_sha256({"STRATEGY_ID": STRATEGY_ID, "LIBRARY_SHA": library_sha, "SPEC": strategy_spec()})

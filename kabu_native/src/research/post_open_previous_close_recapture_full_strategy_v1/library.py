"""Frozen one-candidate library. SHA before economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.post_open_previous_close_recapture_full_strategy_v1 import (
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
        "RAW_FIELD": "PreviousClose",
        "CONFIRMATION": "Observed Trade Update (TradingVolume increase AND CurrentPrice finite > 0)",
        "BELOW": "OTU CurrentPrice < PreviousClose (strict <); continuous only",
        "RECAPTURE": "subsequent OTU CurrentPrice > PreviousClose (strict >) after below regime",
        "EPISODE_START": "first OTU below PreviousClose; anchor = PreviousClose at that ingress; immutable",
        "ENTRY": "the recapture Observed Trade Update itself; no extra confirmation",
        "ENTRY_TIME": "that Observed Trade Update INGRESS",
        "EXECUTION": EXECUTION_ID,
        "SHARES": int(SHARES_N),
        "CAP": int(POSITION_CAP_N),
        "TECHNICAL_EXIT": EXIT_ID,
        "EXIT_FIRE": "post-fill OTU with CurrentPrice < frozen PreviousClose (strict <)",
        "EXIT_FILL": "canonical first causal Bid1; no synthetic Bid",
        "SAME_EPISODE_REENTRY": False,
        "NEW_EPISODE_REENTRY": True,
        "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
        "SPECIAL_QUOTE_ENTRY": False,
        "PREOPEN_ENTRY": False,
        "FORBIDDEN": [
            "bps/tick/ATR",
            "MA/VWAP/RCI/BB",
            "volume threshold",
            "duration",
            "holding-time EXIT",
            "CalcPrice",
            "CurrentPriceStatus",
            "board",
            "11:28 cutoff",
        ],
    }


def freeze_library_sha(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec if spec is not None else strategy_spec())


def freeze_strategy_sha(library_sha: str) -> str:
    return dumps_sha256({"STRATEGY_ID": STRATEGY_ID, "LIBRARY_SHA": library_sha, "SPEC": strategy_spec()})

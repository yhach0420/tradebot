"""Precommitted candidate library. SHA before counts and economics."""
from __future__ import annotations

from typing import Any

from research.causal_mechanism_representation_expansion_v1.spec import dumps_sha256
from research.intraday_special_quote_resolution_full_strategy_v1 import (
    CANDIDATE_C,
    CANDIDATE_D,
    CANDIDATE_U,
    EXECUTION_ID,
    EXIT_D,
    EXIT_U,
    POSITION_CAP_N,
    SESSION_FLATTEN_HM,
    SHARES_N,
    THESIS_D,
    THESIS_U,
)

ENGINE_EVENT_ORDER = (
    "Capture file order via iter_push. ISQ FSM and ISQ_OBSERVED_TRADE_BAR_1M use ingress "
    "received_at/received_at_jst/persisted_at/received_at_utc only. Canonical X1/Bid exit uses "
    "unchanged BoardTape t from capture_event_epoch + BidTime/AskTime freshness inside evaluate_execution "
    "/ first_causal_bid. This package does not implement a new freshness rule."
)


def thesis_u_spec() -> dict[str, Any]:
    return {
        "THESIS_ID": THESIS_U,
        "PRECONDITION": "UP_RELEASE (RELEASE_PRICE > PRE_SPECIAL_PRICE)",
        "ENTRY": "ACCEPT_BAR.Close > RELEASE_PRICE",
        "THESIS_ANCHOR": "RELEASE_PRICE",
        "TECHNICAL_INVALIDATION": "first later eligible ISQ_OBSERVED_TRADE_BAR_1M Close < RELEASE_PRICE",
        "EXIT_ID": EXIT_U,
        "LOW_CONDITION": False,
        "SECOND_CONFIRMATION_BAR": False,
    }


def thesis_d_spec() -> dict[str, Any]:
    return {
        "THESIS_ID": THESIS_D,
        "PRECONDITION": "DOWN_RELEASE (RELEASE_PRICE < PRE_SPECIAL_PRICE)",
        "ENTRY": "ACCEPT_BAR.Close > PRE_SPECIAL_PRICE",
        "THESIS_ANCHOR": "PRE_SPECIAL_PRICE",
        "TECHNICAL_INVALIDATION": "first later eligible ISQ_OBSERVED_TRADE_BAR_1M Close < PRE_SPECIAL_PRICE",
        "EXIT_ID": EXIT_D,
        "LOW_CONDITION": False,
        "SECOND_CONFIRMATION_BAR": False,
    }


def library_spec() -> dict[str, Any]:
    return {
        "MARKET_STATE": ["SPECIAL_QUOTE", "CONTINUOUS_TRADING"],
        "SPECIAL_QUOTE_DIRECTION_USED": False,
        "AVAILABILITY_CLOCK": "INGRESS",
        "TRADE_UPDATE": "TradingVolume increase AND finite CurrentPrice > 0; not CurrentPriceTime identity",
        "TRADE_BAR": "ISQ_OBSERVED_TRADE_BAR_1M from OBSERVED_TRADE_UPDATE only; no CurrentPrice carry",
        "EPISODE": "already-open continuous → SPECIAL_QUOTE after a post-open observed trade; not opening/preopen",
        "PRE_SPECIAL": "last OBSERVED_TRADE_PRICE strictly before the special-transition event",
        "RELEASE": "first CONTINUOUS OBSERVED_TRADE_UPDATE at or after return to CONTINUOUS_TRADING",
        "DIRECTION": "strict > / < / == vs PRE_SPECIAL_PRICE; FLAT_RELEASE no long",
        "ACCEPT_MINUTE": "immediately following full clock minute after RELEASE_MINUTE; no wait to +2",
        "THESIS_U": thesis_u_spec(),
        "THESIS_D": thesis_d_spec(),
        "CANDIDATES": [
            {
                "STRATEGY_ID": CANDIDATE_U,
                "THESES": [THESIS_U],
                "EXECUTION": EXECUTION_ID,
                "CAP": int(POSITION_CAP_N),
                "SHARES": int(SHARES_N),
                "SAME_EPISODE_REENTRY": False,
                "NEW_EPISODE_REENTRY": True,
                "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
            },
            {
                "STRATEGY_ID": CANDIDATE_D,
                "THESES": [THESIS_D],
                "EXECUTION": EXECUTION_ID,
                "CAP": int(POSITION_CAP_N),
                "SHARES": int(SHARES_N),
                "SAME_EPISODE_REENTRY": False,
                "NEW_EPISODE_REENTRY": True,
                "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
            },
            {
                "STRATEGY_ID": CANDIDATE_C,
                "THESES": [THESIS_U, THESIS_D],
                "EXECUTION": EXECUTION_ID,
                "CAP": int(POSITION_CAP_N),
                "SHARES": int(SHARES_N),
                "SAME_EPISODE_REENTRY": False,
                "NEW_EPISODE_REENTRY": True,
                "SESSION_CLOSE": list(SESSION_FLATTEN_HM),
                "NOTE": "At each episode only one thesis can fire; Combined routes by release direction.",
            },
        ],
        "ENGINE_EVENT_ORDER": ENGINE_EVENT_ORDER,
        "FORBIDDEN": [
            "Sign buy/sell",
            "Low split",
            "bps/tick/ATR/duration threshold",
            "second confirmation bar",
            "wait-window",
            "universal EXIT",
            "fixed holding-time EXIT",
            "Z3 candidate EXIT",
        ],
    }


def freeze_library_sha(spec: dict[str, Any] | None = None) -> str:
    return dumps_sha256(spec if spec is not None else library_spec())


def freeze_strategy_sha(strategy_id: str, library_sha: str) -> str:
    return dumps_sha256(
        {
            "STRATEGY_ID": strategy_id,
            "LIBRARY_SHA": library_sha,
            "SPEC": library_spec(),
        }
    )


def candidate_meta(cid: str) -> dict[str, Any]:
    if cid == CANDIDATE_U:
        return {
            "STRATEGY_ID": cid,
            "THESES": [THESIS_U],
            "ENTRY_EXACT": thesis_u_spec()["ENTRY"],
            "THESIS_ANCHOR": "RELEASE_PRICE",
            "TECHNICAL_EXIT": EXIT_U,
            "EXECUTION": EXECUTION_ID,
            "SINGLE_THESIS": True,
        }
    if cid == CANDIDATE_D:
        return {
            "STRATEGY_ID": cid,
            "THESES": [THESIS_D],
            "ENTRY_EXACT": thesis_d_spec()["ENTRY"],
            "THESIS_ANCHOR": "PRE_SPECIAL_PRICE",
            "TECHNICAL_EXIT": EXIT_D,
            "EXECUTION": EXECUTION_ID,
            "SINGLE_THESIS": True,
        }
    return {
        "STRATEGY_ID": cid,
        "THESES": [THESIS_U, THESIS_D],
        "ENTRY_EXACT": "U if UP_RELEASE else D if DOWN_RELEASE; FLAT none",
        "THESIS_ANCHOR": "RELEASE_PRICE if U else PRE_SPECIAL_PRICE",
        "TECHNICAL_EXIT": f"{EXIT_U}|{EXIT_D}",
        "EXECUTION": EXECUTION_ID,
        "SINGLE_THESIS": False,
    }

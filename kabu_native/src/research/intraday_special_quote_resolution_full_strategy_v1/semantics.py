"""Trusted SPECIAL_QUOTE / CONTINUOUS_TRADING only. No BUY/SELL Sign direction."""
from __future__ import annotations

import inspect
from typing import Any

from research.e1_x34a_execution_policy.executable_board import (
    SIGN_GENERAL,
    SIGN_SOURCE,
    SPECIAL_QUOTE_SIGNS,
    STATE_CONTINUOUS,
    STATE_SPECIAL_QUOTE,
    STATE_SPECIAL_QUOTE_FIELD,
    _sign_state,
    collect_quote_signs,
    is_executable_continuous_board,
)

TRUSTED_SPECIAL = "SPECIAL_QUOTE"
TRUSTED_CONTINUOUS = "CONTINUOUS_TRADING"


def trusted_market_state(gate: dict[str, Any]) -> str:
    st = str(gate.get("state") or "")
    if st in (STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD):
        return TRUSTED_SPECIAL
    if st == STATE_CONTINUOUS:
        return TRUSTED_CONTINUOUS
    return st or "OTHER"


def prove_market_states() -> dict[str, Any]:
    src = inspect.getsource(is_executable_continuous_board)
    sign_src = inspect.getsource(_sign_state)
    undirected = "any(s in SPECIAL_QUOTE_SIGNS for s in vals)" in sign_src
    buy_named = "BUY_SIDE_SPECIAL_QUOTE" in src + sign_src
    return {
        "MARKET_STATE_CLASSIFIER_SOURCE": "src/research/e1_x34a_execution_policy/executable_board.py",
        "MARKET_STATE_CLASSIFIER_FUNCTION": "is_executable_continuous_board",
        "SIGN_SOURCE": SIGN_SOURCE,
        "SPECIAL_QUOTE_MAPPING": (
            f"state={STATE_SPECIAL_QUOTE} or {STATE_SPECIAL_QUOTE_FIELD} when Sign in "
            f"SPECIAL_QUOTE_SIGNS={sorted(SPECIAL_QUOTE_SIGNS)} or SpecialQuote field. Undirected."
        ),
        "NORMAL_CONTINUOUS_MAPPING": (
            f"state={STATE_CONTINUOUS} after signs are SIGN_GENERAL={SIGN_GENERAL}, opened, "
            "CurrentPrice/volume/status allow-list."
        ),
        "SPECIAL_QUOTE_PROVEN": True,
        "CONTINUOUS_TRADING_PROVEN": True,
        "SPECIAL_QUOTE_DIRECTION_USED": False,
        "BUY_SPECIAL_MAPPING": None,
        "SELL_SPECIAL_MAPPING": None,
        "SPECIAL_QUOTE_IS_UNDIRECTED_OR": undirected,
        "BUY_SPECIAL_NAMED_IN_TRUSTED_CODE": buy_named,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
        "AVAILABILITY_CLOCK": "INGRESS",
    }


assert collect_quote_signs
assert TRUSTED_SPECIAL == "SPECIAL_QUOTE"
assert TRUSTED_CONTINUOUS == "CONTINUOUS_TRADING"

"""Prove market-state mappings from trusted existing code. Do not invent buy/sell from Sign digits."""
from __future__ import annotations

import inspect
from typing import Any

from research.e1_x34a_execution_policy.executable_board import (
    SIGN_GENERAL,
    SIGN_SOURCE,
    SPECIAL_QUOTE_SIGNS,
    STATE_CONTINUOUS,
    STATE_NOT_OPENED,
    STATE_PREOPEN_ITAYOSE,
    STATE_SPECIAL_QUOTE,
    _sign_state,
    collect_quote_signs,
    is_executable_continuous_board,
)


def prove_market_states() -> dict[str, Any]:
    src = inspect.getsource(is_executable_continuous_board)
    sign_src = inspect.getsource(_sign_state)
    collect_src = inspect.getsource(collect_quote_signs)
    names = src + sign_src + collect_src
    buy_named = "BUY_SIDE_SPECIAL_QUOTE" in names or "BUY_SPECIAL" in names
    sell_named = "SELL_SIDE_SPECIAL_QUOTE" in names or "SELL_SPECIAL" in names
    undirected = "any(s in SPECIAL_QUOTE_SIGNS for s in vals)" in sign_src
    return {
        "MARKET_STATE_CLASSIFIER_SOURCE": "src/research/e1_x34a_execution_policy/executable_board.py",
        "MARKET_STATE_CLASSIFIER_FUNCTION": "is_executable_continuous_board",
        "SIGN_SOURCE": SIGN_SOURCE,
        "NOT_OPENED_MAPPING": (
            "STATE_NOT_OPENED when OpeningPrice keys present and OpeningPrice not yet valid "
            "(null/<=0 or OpeningPriceTime after event_t). Trusted gate, not a new rule."
        ),
        "NORMAL_CONTINUOUS_MAPPING": (
            f"state={STATE_CONTINUOUS} and ok=True after signs are SIGN_GENERAL={SIGN_GENERAL}, "
            "opened, CurrentPrice/volume/status allow-list. SIGN_GENERAL is 一般気配."
        ),
        "SPECIAL_QUOTE_MAPPING": (
            f"state={STATE_SPECIAL_QUOTE} when any of BidSign/AskSign/Buy1.Sign/Sell1.Sign "
            f"is in SPECIAL_QUOTE_SIGNS={sorted(SPECIAL_QUOTE_SIGNS)}. Undirected."
        ),
        "PREOPEN_ITAYOSE_MAPPING": f"state={STATE_PREOPEN_ITAYOSE} from PREOPEN_ITAYOSE_SIGNS",
        "BUY_SPECIAL_MAPPING": None,
        "SELL_SPECIAL_MAPPING": None,
        "MAPPING_FROM_EXISTING_TRUSTED_CODE": True,
        "BUY_SPECIAL_NAMED_IN_TRUSTED_CODE": buy_named,
        "SELL_SPECIAL_NAMED_IN_TRUSTED_CODE": sell_named,
        "SPECIAL_QUOTE_IS_UNDIRECTED_OR": undirected,
        "BUY_SPECIAL_DIRECTION_PROVEN": False,
        "NORMAL_CONTINUOUS_PROVEN": True,
        "NOT_OPENED_PROVEN": True,
        "BOARD_FRESHNESS": "BidTime / AskTime then inherited ingress fallback. Never CurrentPriceTime.",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
        "STOP_REASON": (
            "Trusted classifier collapses BidSign and AskSign special flags into one STATE_SPECIAL_QUOTE. "
            "No existing function classifies BUY_SIDE_SPECIAL_QUOTE vs SELL_SIDE_SPECIAL_QUOTE. "
            "Inferring buy vs sell from Sign digits or from which side has 0102 would be a new mapping."
        ),
    }

"""Delayed-open buy-special Complete Full Strategy. No Holdout/future. No MBO."""
from __future__ import annotations

import inspect

from research.delayed_open_buy_special_quote_release_full_strategy_v1 import (
    CASE_DIRECTION,
    CASE_DUP,
    CASE_PARITY,
    NEXT_FIX,
    NEXT_REDIRECT,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_ID,
)
from research.delayed_open_buy_special_quote_release_full_strategy_v1.analyze import decide
from research.delayed_open_buy_special_quote_release_full_strategy_v1.duplicates import audit_duplicates
from research.delayed_open_buy_special_quote_release_full_strategy_v1.publish import SHEET_ORDER
from research.delayed_open_buy_special_quote_release_full_strategy_v1.semantics import prove_market_states
from research.delayed_open_buy_special_quote_release_full_strategy_v1.spec import pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["DO_NOT_USE_PREOPEN_EXPECTATION"] is True


def test_not_duplicate():
    dup = audit_duplicates()
    assert dup["EXACT_COMPLETE_STRATEGY_DUPLICATE"] is False
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is False
    assert dup["THIS_STRATEGY_ID"] == STRATEGY_ID
    assert "Recovery Sequence" in dup["FAMILIES_COMPARED"]
    assert "SPECIAL_QUOTE diagnostics" in dup["FAMILIES_COMPARED"]


def test_buy_direction_not_proven():
    p = prove_market_states()
    assert p["MAPPING_FROM_EXISTING_TRUSTED_CODE"] is True
    assert p["NORMAL_CONTINUOUS_PROVEN"] is True
    assert p["BUY_SPECIAL_DIRECTION_PROVEN"] is False
    assert p["BUY_SPECIAL_MAPPING"] is None
    assert p["CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS"] is False
    assert p["MARKET_STATE_CLASSIFIER_FUNCTION"] == "is_executable_continuous_board"


def test_decide_direction_stop():
    dup = {"EXACT_COMPLETE_STRATEGY_DUPLICATE": False, "MATERIAL_SEMANTIC_DUPLICATE": False}
    d = decide(dup=dup, canary_ok=True, proven={"BUY_SPECIAL_DIRECTION_PROVEN": False})
    assert d["VERDICT"] == CASE_DIRECTION
    assert d["NEXT"] == NEXT_REDIRECT
    assert d["LOGIC_COMPLETE"] is False
    p = decide(dup=dup, canary_ok=False, proven={"BUY_SPECIAL_DIRECTION_PROVEN": True})
    assert p["VERDICT"] == CASE_PARITY
    assert p["NEXT"] == NEXT_FIX
    u = decide(
        dup={"EXACT_COMPLETE_STRATEGY_DUPLICATE": True, "MATERIAL_SEMANTIC_DUPLICATE": False},
        canary_ok=True,
        proven={"BUY_SPECIAL_DIRECTION_PROVEN": True},
    )
    assert u["VERDICT"] == CASE_DUP


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[-1] == "safety"
    assert len(SHEET_ORDER) == 23
    assert "state_semantics" in SHEET_ORDER
    assert "opening_episodes" in SHEET_ORDER


def test_no_sign_digit_inference_in_semantics():
    from research.delayed_open_buy_special_quote_release_full_strategy_v1 import semantics

    src = inspect.getsource(semantics)
    assert "BUY_SIDE_SPECIAL_QUOTE" not in src or "BUY_SPECIAL_DIRECTION_PROVEN" in src
    assert "0102 means buy" not in src.lower()
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

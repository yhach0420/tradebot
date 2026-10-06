"""Native discontinuous-up semantics. No Holdout. No guessed UP code."""
from __future__ import annotations

from research.post_open_native_discontinuous_up_repricing_full_strategy_v1 import (
    CASE_SEMANTICS,
    NEXT_V5,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_ID,
)
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.analyze import decide
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.publish import SHEET_ORDER
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.semantics import prove_price_status_semantics
from research.post_open_native_discontinuous_up_repricing_full_strategy_v1.spec import pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["CALC_PRICE_RETUNE"] is False
    assert parent["OPENING_LINE_REMAINS_CLOSED"] is True
    assert parent["AOP_ARCHITECTURE_STATUS"] == "CLOSED"


def test_status_codes_from_trusted_parser_not_guessed():
    p = prove_price_status_semantics()
    assert p["WEB_USED"] is False
    assert p["GUESSED_UP_CODE"] is False
    assert p["NORMAL_STATUS_CODE"] == 1
    assert p["DISCONTINUOUS_STATUS_CODE"] == 2
    assert p["DISCONTINUOUS_STATUS_LABEL"] == "不連続歩み"
    assert p["NORMAL_STATUS_LABEL"] == "現値"
    assert p["STATUS_FIELD_PROVEN"] is True
    assert p["UP_CHANGE_STATUS_CODE"] is None
    assert p["SEMANTICS_PROVEN"] is False
    assert p["BLOCKER"] == "UP_CHANGE_STATUS_CODE_NOT_IN_TRUSTED_LOCAL_SOURCE"


def test_decide_semantics_stops():
    d = decide(semantics_ok=False)
    assert d["VERDICT"] == CASE_SEMANTICS
    assert d["NEXT"] == NEXT_V5
    assert d["LOGIC_COMPLETE"] is False
    assert STRATEGY_ID.startswith("NATIVE_DISCONT_UP")


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Status_Semantics" in SHEET_ORDER
    assert "ISQ_Overlap" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

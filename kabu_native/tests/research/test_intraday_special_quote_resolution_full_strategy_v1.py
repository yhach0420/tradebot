"""ISQ resolution Full Causal. No Holdout/future. No MBO. No Sign direction."""
from __future__ import annotations

from research.intraday_special_quote_resolution_full_strategy_v1 import (
    CASE_DUP,
    CASE_PARITY,
    CASE_TESTS,
    CANDIDATE_C,
    CANDIDATE_D,
    CANDIDATE_U,
    NEXT_FIX,
    NEXT_V2,
    OPENING_LINE_STATUS,
    REQUIRED_PARENT_VERDICT,
)
from research.intraday_special_quote_resolution_full_strategy_v1.analyze import decide
from research.intraday_special_quote_resolution_full_strategy_v1.duplicates import audit_duplicates
from research.intraday_special_quote_resolution_full_strategy_v1.publish import SHEET_ORDER
from research.intraday_special_quote_resolution_full_strategy_v1.semantics import prove_market_states
from research.intraday_special_quote_resolution_full_strategy_v1.spec import pin_parent
from research.intraday_special_quote_resolution_full_strategy_v1.synthetic import run_synthetic_tests
from research.intraday_special_quote_resolution_full_strategy_v1.trade import INGRESS_KEYS
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["OPENING_CURRENT_DATA_LINE_STATUS"] == "CLOSED"
    assert parent["OPENING_LINE_REMAINS_CLOSED"] is True


def test_not_duplicate():
    dup = audit_duplicates()
    assert dup["THESIS_U_EXACT_DUPLICATE"] is False
    assert dup["THESIS_U_MATERIAL_DUPLICATE"] is False
    assert dup["THESIS_D_EXACT_DUPLICATE"] is False
    assert dup["THESIS_D_MATERIAL_DUPLICATE"] is False
    assert CANDIDATE_U in dup["ELIGIBLE_CANDIDATE_IDS"]
    assert CANDIDATE_D in dup["ELIGIBLE_CANDIDATE_IDS"]
    assert CANDIDATE_C in dup["ELIGIBLE_CANDIDATE_IDS"]
    assert "FAILED_BREAKDOWN_RECLAIM" in dup["FAMILIES_COMPARED"]


def test_semantics_no_sign_direction():
    p = prove_market_states()
    assert p["SPECIAL_QUOTE_PROVEN"] is True
    assert p["CONTINUOUS_TRADING_PROVEN"] is True
    assert p["SPECIAL_QUOTE_DIRECTION_USED"] is False
    assert p["CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS"] is False
    assert "CurrentPriceTime" not in INGRESS_KEYS


def test_synthetic_30():
    got = run_synthetic_tests()
    failed = [r["id"] for r in got["rows"] if not r.get("PASS")]
    assert got["ALL_PASS"] is True, failed
    assert got["PASS_N"] == 30


def test_decide_stops():
    dup = {"ELIGIBLE_CANDIDATE_IDS": [CANDIDATE_U, CANDIDATE_D, CANDIDATE_C]}
    t = decide(dup=dup, tests_ok=False, canary_ok=True)
    assert t["VERDICT"] == CASE_TESTS
    assert t["NEXT"] == NEXT_FIX
    p = decide(dup=dup, tests_ok=True, canary_ok=False)
    assert p["VERDICT"] == CASE_PARITY
    u = decide(dup={"ELIGIBLE_CANDIDATE_IDS": []}, tests_ok=True, canary_ok=True)
    assert u["VERDICT"] == CASE_DUP
    assert u["NEXT"] == NEXT_V2
    assert u["OPENING_CURRENT_DATA_LINE_STATUS"] == OPENING_LINE_STATUS


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[-1] == "safety"
    assert "duplicate_check" in SHEET_ORDER
    assert "unit_tests" in SHEET_ORDER
    assert "selected_logic" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

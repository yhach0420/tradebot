"""CalcPrice lead Full Causal. No Holdout/future. No MBO. No AOP rescue."""
from __future__ import annotations

from research.post_open_calc_price_lead_acceptance_full_strategy_v1 import (
    CASE_DUP,
    CASE_INDEP,
    CASE_INPUT,
    CASE_SEMANTICS,
    CASE_TESTS,
    NEXT_FIX,
    NEXT_V4,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_ID,
)
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.analyze import decide
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.calc import INGRESS_KEYS
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.duplicates import audit_duplicates
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.publish import SHEET_ORDER
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.semantics import prove_calcprice_semantics
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.spec import pin_parent
from research.post_open_calc_price_lead_acceptance_full_strategy_v1.synthetic import run_synthetic_tests
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["AOP_ARCHITECTURE_STATUS"] == "CLOSED"
    assert parent["OPENING_LINE_REMAINS_CLOSED"] is True


def test_semantics_label():
    p = prove_calcprice_semantics()
    assert p["OFFICIAL_MEANING"] == "計算用現値 (BoardSuccess CalcPrice)"
    assert p["IN_BOARDSUCCESS"] is True
    assert p["DEDICATED_FIELD_TIMESTAMP"] is False
    assert p["FORMULA_PROVEN"] is False
    assert "CurrentPriceTime" not in INGRESS_KEYS
    assert p["SEMANTICS_PROVEN"] is True


def test_not_duplicate_without_tob_copy():
    dup = audit_duplicates(eq_current=0.1, eq_bid=0.1, eq_ask=0.1, eq_mid=0.1)
    assert dup["EXACT_DUPLICATE"] is False
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is False
    assert "AOP" in dup["FAMILIES_COMPARED"]
    assert STRATEGY_ID.startswith("CALC_LEAD")


def test_mid_copy_is_material():
    dup = audit_duplicates(eq_current=0.2, eq_bid=0.1, eq_ask=0.1, eq_mid=0.995)
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is True


def test_synthetic():
    got = run_synthetic_tests()
    failed = [r["id"] for r in got["rows"] if not r.get("PASS")]
    assert got["ALL_PASS"] is True, failed
    assert got["PASS_N"] >= 30


def test_decide_stops():
    assert decide(semantics_ok=False)["VERDICT"] == CASE_SEMANTICS
    assert decide(semantics_ok=True, indep_ok=False)["VERDICT"] == CASE_INDEP
    assert decide(semantics_ok=True, indep_ok=True, input_ok=False)["VERDICT"] == CASE_INPUT
    assert decide(semantics_ok=True, input_ok=True, indep_ok=True, dup_ok=False)["VERDICT"] == CASE_DUP
    t = decide(semantics_ok=True, input_ok=True, indep_ok=True, dup_ok=True, tests_ok=False)
    assert t["VERDICT"] == CASE_TESTS
    assert t["NEXT"] == NEXT_FIX
    assert decide(semantics_ok=False)["NEXT"] == NEXT_V4


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "CalcPrice_Semantics" in SHEET_ORDER
    assert "Independence" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

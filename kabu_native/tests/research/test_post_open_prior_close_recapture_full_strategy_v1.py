"""Prior-close recapture Full Causal. No Holdout/future. No MBO. No gap-size."""
from __future__ import annotations

from research.post_open_prior_close_recapture_full_strategy_v1 import (
    CASE_COVERAGE,
    CASE_DUP,
    CASE_INPUT,
    CASE_SEMANTICS,
    CASE_TESTS,
    NEXT_FIX,
    NEXT_V6,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_ID,
)
from research.post_open_prior_close_recapture_full_strategy_v1.analyze import decide
from research.post_open_prior_close_recapture_full_strategy_v1.duplicates import audit_duplicates
from research.post_open_prior_close_recapture_full_strategy_v1.fields import INGRESS_KEYS
from research.post_open_prior_close_recapture_full_strategy_v1.publish import SHEET_ORDER
from research.post_open_prior_close_recapture_full_strategy_v1.semantics import prove_previous_close_semantics
from research.post_open_prior_close_recapture_full_strategy_v1.spec import pin_parent
from research.post_open_prior_close_recapture_full_strategy_v1.synthetic import run_synthetic_tests
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["NATIVE_STATUS_LINE"] == "CLOSED"
    assert parent["OPENING_LINE_REMAINS_CLOSED"] is True
    assert parent["CALC_PRICE_RETUNE"] is False
    assert parent["NATIVE_STATUS_RESCUE"] is False


def test_semantics_label():
    p = prove_previous_close_semantics()
    assert p["OFFICIAL_NAME"] == "PreviousClose"
    assert "前日終値" in str(p["OFFICIAL_MEANING"])
    assert p["IN_BOARDSUCCESS"] is True
    assert p["DEDICATED_FIELD_TIMESTAMP"] is True
    assert p["TIMESTAMP_NAME"] == "PreviousCloseTime"
    assert "CurrentPriceTime" not in INGRESS_KEYS
    assert p["WEB_USED"] is False
    assert p["SEMANTICS_PROVEN"] is True


def test_not_duplicate_of_reclaim_families():
    dup = audit_duplicates(special_quote_overlap_rate=0.0)
    assert dup["EXACT_DUPLICATE"] is False
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is False
    assert "VWAP_RECLAIM_REJECTION" in dup["FAMILIES_COMPARED"]
    assert "FAILED_BREAKDOWN_RECLAIM" in dup["FAMILIES_COMPARED"]
    assert "GAP_FILL_TO_PRIOR_CLOSE (inventory considered, never built)" in dup["FAMILIES_COMPARED"]
    assert STRATEGY_ID == "PRIOR_CLOSE_RECAPTURE_X1_Z_PRIOR_CLOSE_LOSS_V1"


def test_special_quote_overlap_is_material():
    dup = audit_duplicates(special_quote_overlap_rate=0.995)
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is True


def test_synthetic():
    got = run_synthetic_tests()
    failed = [r["id"] for r in got["rows"] if not r.get("PASS")]
    assert got["ALL_PASS"] is True, failed
    assert got["PASS_N"] >= 30
    assert got["TOTAL"] >= 35


def test_decide_stops():
    assert decide(semantics_ok=False)["VERDICT"] == CASE_SEMANTICS
    assert decide(semantics_ok=True, input_ok=False)["VERDICT"] == CASE_INPUT
    assert decide(semantics_ok=True, input_ok=True, dup_ok=False)["VERDICT"] == CASE_DUP
    t = decide(semantics_ok=True, input_ok=True, dup_ok=True, tests_ok=False)
    assert t["VERDICT"] == CASE_TESTS
    assert t["NEXT"] == NEXT_FIX
    assert decide(semantics_ok=False)["NEXT"] == NEXT_V6
    assert decide(semantics_ok=True, input_ok=True, dup_ok=True, tests_ok=True, canary_ok=True, coverage_ok=False)["VERDICT"] == CASE_COVERAGE


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "PreviousClose_Semantics" in SHEET_ORDER
    assert "Recapture_Events" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

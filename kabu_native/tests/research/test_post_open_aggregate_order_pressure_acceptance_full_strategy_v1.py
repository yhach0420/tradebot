"""AOP Full Causal. No Holdout/future. No MBO. No Opening/ISQ retune."""
from __future__ import annotations

from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1 import (
    CASE_DUP,
    CASE_INPUT,
    CASE_PARITY,
    CASE_TESTS,
    NEXT_FIX,
    NEXT_V3,
    OPENING_LINE_STATUS,
    REQUIRED_PARENT_VERDICT,
    STRATEGY_ID,
)
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.analyze import decide
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.duplicates import audit_duplicates
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.publish import SHEET_ORDER
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.semantics import prove_field_semantics
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.spec import pin_parent
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.synthetic import run_synthetic_tests
from research.post_open_aggregate_order_pressure_acceptance_full_strategy_v1.pressure import INGRESS_KEYS
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["OPENING_CURRENT_DATA_LINE_STATUS"] == "CLOSED"
    assert parent["ISQ_RETUNE"] is False


def test_semantics():
    p = prove_field_semantics()
    assert p["SEMANTICS_PROVEN"] is True
    assert p["PREOPEN_ONLY_IN_SCHEMA"] is False
    assert p["DEDICATED_FIELD_TIMESTAMP"] is False
    assert "CurrentPriceTime" not in INGRESS_KEYS


def test_not_duplicate():
    dup = audit_duplicates()
    assert dup["EXACT_DUPLICATE"] is False
    assert dup["MATERIAL_SEMANTIC_DUPLICATE"] is False
    assert "OPENING_AUCTION_CONTEXT" in dup["FAMILIES_COMPARED"]
    assert "IOAR" in dup["FAMILIES_COMPARED"]
    assert STRATEGY_ID == "AOP_DUAL_BUY_PRESSURE_X1_Z_ANCHOR_LOSS_V1"


def test_synthetic():
    got = run_synthetic_tests()
    failed = [r["id"] for r in got["rows"] if not r.get("PASS")]
    assert got["ALL_PASS"] is True, failed
    assert got["PASS_N"] >= 30
    assert got["TOTAL"] >= 30


def test_decide_stops():
    t = decide(input_ok=False)
    assert t["VERDICT"] == CASE_INPUT
    assert t["NEXT"] == NEXT_V3
    d = decide(input_ok=True, dup_ok=False)
    assert d["VERDICT"] == CASE_DUP
    u = decide(input_ok=True, dup_ok=True, tests_ok=False)
    assert u["VERDICT"] == CASE_TESTS
    assert u["NEXT"] == NEXT_FIX
    p = decide(input_ok=True, dup_ok=True, tests_ok=True, canary_ok=False)
    assert p["VERDICT"] == CASE_PARITY
    assert p["OPENING_CURRENT_DATA_LINE_STATUS"] == OPENING_LINE_STATUS


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Post_Open_Dynamics" in SHEET_ORDER
    assert "Precommit" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

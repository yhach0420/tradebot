"""DISCOVERY_INFORMATION_OBJECT_EXPANSION_DECISION_V1. No PnL. Parent/IOAR/UEIA pins."""
from __future__ import annotations

import inspect

import pytest

from research.discovery_information_object_expansion_decision_v1 import (
    ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN,
    CASE_A,
    CASE_B,
    CURRENT_42_RETUNE,
    CURRENT_O1_O2_O3_LINE_CLOSED,
    NEXT_A,
    NEXT_B,
    PREOPEN_EXECUTION_VALID,
    STATUSES,
)
from research.discovery_information_object_expansion_decision_v1.analyze import run_decision
from research.discovery_information_object_expansion_decision_v1.objects import (
    FORBIDDEN_DEPTH_TRANSFORMS,
    catalog,
    freeze_spec,
    select_one,
)
from research.discovery_information_object_expansion_decision_v1.publish import SHEET_ORDER
from research.discovery_information_object_expansion_decision_v1.schema_audit import assert_dev_only_day
from research.discovery_information_object_expansion_decision_v1.spec import pin_ioar, pin_parent, pin_ueia
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def _schema_ok() -> dict:
    days = [
        "20260722",
        "20260728",
        "20260729",
        "20260730",
        "20260731",
        "20260803",
        "20260804",
        "20260805",
        "20260806",
        "20260807",
    ]
    cov = {
        oid: {
            "STORED": True,
            "DEV_DAY_N": 10,
            "SYMBOL_COVERAGE": 40,
            "SESSION_COVERAGE": "AM",
            "BROAD": True,
            "CAUSALLY_TIMESTAMPED": True,
        }
        for oid in (
            "PRICE_PATH",
            "TRADED_ACTIVITY",
            "TOP_OF_BOOK_STATE",
            "FULL_DEPTH_GEOMETRY",
            "DEPTH_MIGRATION",
            "QUOTE_UPDATE_DYNAMICS",
            "TRADE_FLOW_DYNAMICS",
            "ABSORPTION_REPLENISHMENT",
            "OPENING_AUCTION_CONTEXT",
            "PRIOR_SESSION_CONTEXT",
            "CROSS_SECTIONAL_FLOW_STATE",
            "PORTFOLIO_STATE",
            "EVENT_FLOW_DYNAMICS",
        )
    }
    return {
        "ok": True,
        "coverage_by_object": cov,
        "full_depth_available": True,
        "event_flow_available": True,
        "preopen_available": True,
        "prior_session_available": True,
        "timestamp_semantics_proven": True,
        "days_requested": days,
    }


def test_parent_case_c_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["CASE"] == "C"
    assert parent["MECHANISM_LIBRARY_N"] == 42
    assert parent["SELECTABLE_FULL_CAUSAL_CANDIDATE_N"] == 25
    assert parent["COVERAGE_PASS_N"] == 10
    assert parent["G1_G5_SURVIVOR_N"] == 0
    assert parent["BASE_QUALIFIED_N"] == 0
    assert parent["CURRENT_O1_O2_O3_LINE_CLOSED"] is True
    assert parent["CURRENT_42_RETUNE"] is False
    assert parent["ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN"] is False


def test_current_42_closed_no_retune():
    assert CURRENT_O1_O2_O3_LINE_CLOSED is True
    assert CURRENT_42_RETUNE is False
    assert ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN is False
    assert PREOPEN_EXECUTION_VALID is False


def test_ioar_and_ueia_pins():
    ioar = pin_ioar()
    ueia = pin_ueia()
    assert ioar["ok"] is True
    assert ioar["final_verdict"] == "IOAR_STRATEGY_REJECTED"
    assert ioar["EXACT_MECHANISM_CLOSED"] is True
    assert ioar["BROADER_ORDER_FLOW_FAMILY_AUTOMATICALLY_CLOSED"] is False
    assert ueia["ok"] is True
    assert ueia["final_verdict"] == "UEIA_NO_VALIDATED_EDGE"
    assert ueia["UEIA_REOPEN"] is False


def test_catalog_statuses_and_no_unexplored():
    rows = catalog()
    assert "UNEXPLORED" not in STATUSES
    for r in rows:
        assert r["STATUS"] in STATUSES
    ids = [r["OBJECT_ID"] for r in rows]
    assert "FULL_DEPTH_GEOMETRY" in ids
    assert "ABSORPTION_REPLENISHMENT" in ids
    absb = [r for r in rows if r["OBJECT_ID"] == "ABSORPTION_REPLENISHMENT"][0]
    assert absb["STATUS"] == "EXACT_MECHANISM_CLOSED_OBJECT_BROADER"
    assert absb["ELIGIBLE"] if False else absb["I5_NOT_EXACT_CLOSED_PRIMARY"] is False


def test_select_full_depth_not_ioar_ueia():
    packed = run_decision(_schema_ok())
    assert packed["decision"]["VERDICT"] == CASE_A
    assert packed["decision"]["NEXT"] == NEXT_A
    assert packed["decision"]["CURRENT_42_RETUNE"] is False
    sel = packed["selected"]
    assert sel is not None
    assert sel["OBJECT_ID"] == "FULL_DEPTH_GEOMETRY"
    assert sel["OBJECT_ID"] != "ABSORPTION_REPLENISHMENT"
    spec = packed["spec"]
    assert spec["ABSORPTION_REVERSAL_EXACT_MECHANISM_SELECTED"] is False
    assert spec["UEIA_REPRODUCTION"] is False
    assert spec["THRESHOLD_VARIATION_ONLY"] is False
    assert spec["ENTRY_RULE"] is None
    assert spec["EXIT_RULE"] is None
    for name in ("canonical_depth_imbalance", "depth_imb_d3", "depth_imb_d5", "depth_imb_d10", "threshold_optimization"):
        assert name in FORBIDDEN_DEPTH_TRANSFORMS
        assert name in spec["FORBIDDEN_TRANSFORMATIONS"]
    assert packed["decision"]["EXACT_IOAR"] is False
    assert packed["decision"]["EXACT_UEIA_REPRODUCTION"] is False
    assert packed["decision"]["EXACT_CLOSED_BOARD_MECHANISM"] is False
    assert packed["decision"]["OUTCOME_READ_N"] == 0
    assert packed["decision"]["ECONOMICS_RUN"] is False
    assert len(packed["eligible"]) <= 3
    assert packed["decision"]["ELIGIBLE_INFORMATION_OBJECT_N"] >= 1


def test_case_b_when_no_eligible():
    schema = _schema_ok()
    for oid, rec in schema["coverage_by_object"].items():
        rec["STORED"] = False
        rec["DEV_DAY_N"] = 0
        rec["BROAD"] = False
        rec["CAUSALLY_TIMESTAMPED"] = False
    packed = run_decision(schema)
    assert packed["eligible"] == []
    assert packed["decision"]["VERDICT"] == CASE_B
    assert packed["decision"]["NEXT"] == NEXT_B
    assert packed["decision"]["SELECTED_OBJECT_ID"] is None


def test_no_outcome_mining_in_analyze():
    import research.discovery_information_object_expansion_decision_v1.analyze as analyze
    import research.discovery_information_object_expansion_decision_v1.objects as objects
    import research.discovery_information_object_expansion_decision_v1.schema_audit as schema_audit

    for mod in (analyze, objects, schema_audit):
        src = inspect.getsource(mod)
        assert "pack_trades" not in src
        assert "TOTAL_PNL" not in src
        assert "H5" not in src
        assert "SHAP" not in src
        assert "roc_auc" not in src.lower()
        assert "MFE" not in src


def test_sheet_order():
    assert SHEET_ORDER == (
        "answers",
        "objective_alignment",
        "parent",
        "prior_use",
        "ioar",
        "ueia",
        "raw_objects",
        "full_depth",
        "event_flow",
        "preopen",
        "prior_session",
        "classification",
        "eligibility",
        "selection",
        "decision",
        "safety",
    )


def test_holdout_stress_blocked():
    with pytest.raises(RuntimeError, match="HOLDOUT"):
        assert_dev_only_day(BURNED_HOLDOUT_DAYS[0])
    with pytest.raises(RuntimeError, match="STRESS"):
        assert_dev_only_day(STRESS_DAYS[0])
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260903")
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260907")


def test_freeze_spec_stable():
    packed = run_decision(_schema_ok())
    again = freeze_spec(packed["selected"], eligible_n=len(packed["eligible"]))
    assert again["INFORMATION_OBJECT_SPEC_SHA256"] == packed["spec"]["INFORMATION_OBJECT_SPEC_SHA256"]
    assert select_one(packed["eligible"])["OBJECT_ID"] == "FULL_DEPTH_GEOMETRY"

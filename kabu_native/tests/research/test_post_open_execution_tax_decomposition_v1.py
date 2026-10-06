"""Execution-tax decomposition. No Holdout. No new ENTRY. Canonical W5 only."""
from __future__ import annotations

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.entry_execution_feasibility.fill import standalone_fill
from research.post_open_execution_tax_decomposition_v1 import (
    ANCHOR_HMS,
    CONDITIONAL_FEATURES,
    EXPECTED_ANCHOR_N,
    FEATURE_IDS,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    W5_WAIT_SEC,
)
from research.post_open_execution_tax_decomposition_v1.harvest import day_overlay_key, join_overlay_key
from research.post_open_execution_tax_decomposition_v1.publish import SHEET_ORDER
from research.post_open_execution_tax_decomposition_v1.spec import pin_parent, pin_w5
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["CANDIDATE_STRATEGY_N"] == 0


def test_w5_identity():
    w5 = pin_w5()
    assert w5["ok"] is True
    assert float(W5_WAIT_SEC) == 5.0
    assert w5["repricing"] is False
    assert w5["chase"] is False
    assert w5["fallback_ask"] is False
    assert "find_ask_cross_fill" in str(w5.get("cross_function") or "")
    src = find_ask_cross_fill.__code__.co_varnames
    assert "limit_price" in src
    assert "wait_sec" in src
    assert standalone_fill.__module__ == "research.entry_execution_feasibility.fill"


def test_population_constants_and_features():
    assert EXPECTED_ANCHOR_N == 6500
    assert len(ANCHOR_HMS) == 13
    assert (9, 0) not in ANCHOR_HMS
    assert len(FEATURE_IDS) == 30
    assert "ASK_UPDATE_N_60S" not in CONDITIONAL_FEATURES
    assert CONDITIONAL_FEATURES[0] == "SPREAD_BPS"


def test_overlay_join_key_includes_date():
    assert day_overlay_key("7203", "09:10") == "7203|09:10"
    a = join_overlay_key("20260722", "7203", "09:10")
    b = join_overlay_key("20260728", "7203", "09:10")
    assert a != b
    assert a.startswith("20260722|")
    assert join_overlay_key("20260722", *day_overlay_key("7203", "09:10").split("|", 1)) == a


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Common_Endpoint" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS

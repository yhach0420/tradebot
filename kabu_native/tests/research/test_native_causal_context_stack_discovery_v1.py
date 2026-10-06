"""Tests for native causal context-stack discovery. No strategy. No Confirmation."""
from __future__ import annotations

import inspect

from research.native_causal_context_stack_discovery_v1 import (
    CASE_FOUND,
    CASE_NO_RULE,
    CASE_PARTIAL,
    CASE_UNSTABLE,
    DETECTOR_RETUNED,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FEATURE_NAMES,
    FROZEN_VALIDATION_OPENED,
    I2_REOPENED,
    MAX_FAVORABLE_RULES,
    OLD_CONFIRMATION_OPENED,
    PNL_OPTIMIZATION,
    REFRACTORY_BARS,
    TREE_CRITERION,
    TREE_MAX_DEPTH,
    TREE_MIN_LEAF_ABS,
    TREE_MIN_LEAF_FRAC,
)
from research.native_causal_context_stack_discovery_v1.analyze import build_report_body, decide
from research.native_causal_context_stack_discovery_v1.isolation import OUT, write_overlap_n
from research.native_causal_context_stack_discovery_v1.publish import SHEET_ORDER
from research.native_causal_context_stack_discovery_v1.spec import source_sha256
from research.native_causal_context_stack_discovery_v1.tree import min_leaf_n
from research.native_causal_context_stack_discovery_v1.walk import walk_events
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory, displacement
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def test_hashes_and_closed_paths():
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert DETECTOR_RETUNED is False
    assert I2_REOPENED is False
    assert PNL_OPTIMIZATION is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert REFRACTORY_BARS == 5
    assert TREE_MAX_DEPTH == 3
    assert TREE_CRITERION == "gini"
    assert TREE_MIN_LEAF_ABS == 500
    assert TREE_MIN_LEAF_FRAC == 0.01
    assert MAX_FAVORABLE_RULES == 3
    assert source_sha256()
    assert len(source_sha256()) == 64
    assert min_leaf_n(10_000) == 500
    assert min_leaf_n(80_000) == 800


def test_no_strategy_or_indicator_catalog():
    src = inspect.getsource(walk_events) + inspect.getsource(decide) + inspect.getsource(build_report_body)
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert "RandomForest" not in src
    assert "XGB" not in src
    assert "RSI" not in src
    assert "MACD" not in src
    assert "Bollinger" not in src
    assert "participation_expand" not in inspect.getsource(walk_events)
    assert "_classify" not in inspect.getsource(walk_events)
    assert "I2" not in inspect.getsource(walk_events)
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(walk_events)
    assert "tv_clock_pctl" in FEATURE_NAMES
    assert "dist_support_atr" in FEATURE_NAMES


def test_displacement_has_no_tv_gate():
    src = inspect.getsource(displacement)
    assert "tv" not in src.lower() or "ClockHistory" not in src
    assert "0.80" not in src


def test_clock_prior_days_only():
    c = ClockHistory()
    rec = {"t": ["10:00"], "va": [1.0], "v": [1.0]}
    assert c.tv_pctl("7203", "10:00", 1.0) is None
    c.commit_day("7203", rec, [0])
    assert c.tv_pctl("7203", "10:00", 1.0) is None


def test_verdicts_and_sheets():
    assert CASE_FOUND.startswith("NATIVE_CONTEXT_STACK")
    assert CASE_PARTIAL.startswith("NATIVE_CONTEXT_STACK")
    assert CASE_NO_RULE.startswith("NATIVE_CONTEXT_STACK")
    assert CASE_UNSTABLE == "CONTEXT_STACK_NOT_STABLE_EVEN_IN_DISCOVERY_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "Event_Generator" in SHEET_ORDER
    assert "Frozen_Rules" in SHEET_ORDER
    assert "Ablation" in SHEET_ORDER
    assert "SR_Contribution" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    d = decide({"unstable_in_discovery": True, "surviving_rule_ids": [], "rule_eval": {}})
    assert d["VERDICT"] == CASE_UNSTABLE
    overlap = write_overlap_n("", "")
    assert overlap == 0
    assert "native_causal_context_stack_discovery_v1" in str(OUT).replace("\\", "/")

"""Tests for R14 trend incrementality RCA. No strategy. No threshold retune."""
from __future__ import annotations

import inspect

from research.native_causal_context_stack_discovery_v1.freeze import event_generator_sha256
from research.native_causal_context_stack_discovery_v1.features import local_structure
from research.one_minute_native_playbook_discovery_v1.states import clock_ret
from research.r14_trend_incrementality_rca_v1 import (
    CASE_INVALID,
    CURRENT_JUDGMENT,
    EXPECTED_EVENT_GENERATOR_SHA256,
    EXPECTED_TREE_SHA256,
    NEW_FEATURE_ADDED,
    PNL_OPTIMIZATION,
    PRIOR5_RANGE_THR,
    PRIOR5_RET_THR,
    R15_THR,
    THRESHOLD_RETUNED,
)
from research.r14_trend_incrementality_rca_v1.analyze import build_report_body, decide
from research.r14_trend_incrementality_rca_v1.isolation import OUT, write_overlap_n
from research.r14_trend_incrementality_rca_v1.publish import SHEET_ORDER
from research.r14_trend_incrementality_rca_v1.rules import FEATURE_SEMANTICS, hit_r14, hit_t15
from research.r14_trend_incrementality_rca_v1.spec import source_sha256
from research.r14_trend_incrementality_rca_v1.walk import walk_events


def test_frozen_hashes_and_thresholds():
    assert event_generator_sha256() == EXPECTED_EVENT_GENERATOR_SHA256
    assert EXPECTED_TREE_SHA256 == "f6abfa7c2f34363a706ec67e70d9a5b0fb9988e6217abd7fd265be63161f318e"
    assert R15_THR == 0.0040059178
    assert PRIOR5_RET_THR == -0.0010161741
    assert PRIOR5_RANGE_THR == 2.2981482
    assert THRESHOLD_RETUNED is False
    assert NEW_FEATURE_ADDED is False
    assert PNL_OPTIMIZATION is False
    assert CURRENT_JUDGMENT == "NATIVE_CONTEXT_STACK_PARTIAL_MECHANISM_V1"
    assert source_sha256()
    assert len(source_sha256()) == 64


def test_r15_and_prior5_not_direction_normalized():
    assert FEATURE_SEMANTICS["r15"]["direction_normalized"] is False
    assert FEATURE_SEMANTICS["prior5_ret"]["direction_normalized"] is False
    src = inspect.getsource(clock_ret) + inspect.getsource(local_structure)
    assert "* sign" not in src
    assert "direction" in inspect.getsource(local_structure)
    assert "pret = float(c0) / float(c5) - 1.0" in inspect.getsource(local_structure)


def test_no_strategy_or_new_features():
    src = inspect.getsource(walk_events) + inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert "RSI" not in src
    assert "MACD" not in src
    assert "RandomForest" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(walk_events)


def test_frozen_rule_hits():
    row = {"r15": 0.005, "prior5_ret": 0.0, "prior5_range_rel": 3.0}
    assert hit_t15(row) is True
    assert hit_r14(row) is True
    row2 = {"r15": 0.003, "prior5_ret": 0.0, "prior5_range_rel": 3.0}
    assert hit_t15(row2) is False
    assert hit_r14(row2) is False
    row3 = {"r15": 0.005, "prior5_ret": 0.0, "prior5_range_rel": 2.0}
    assert hit_t15(row3) is True
    assert hit_r14(row3) is False


def test_invalid_semantics_stops():
    d = decide(invalid=True, nested={}, matched_t15={}, matched_r14={})
    assert d["VERDICT"] == CASE_INVALID
    assert d["advance"] is False
    assert SHEET_ORDER[0] == "Binding"
    assert "Feature_Semantics" in SHEET_ORDER
    assert "Matched_T15" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    assert write_overlap_n("", "") == 0
    assert "r14_trend_incrementality_rca_v1" in str(OUT).replace("\\", "/")

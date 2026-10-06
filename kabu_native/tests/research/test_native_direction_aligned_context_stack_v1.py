"""Tests for direction-aligned native context stack. No strategy. No R14 repair."""
from __future__ import annotations

import inspect
import math

from research.native_causal_context_stack_discovery_v1.freeze import event_generator_sha256
from research.native_direction_aligned_context_stack_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_UNSTABLE,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_EVENT_GENERATOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FEATURE_NAMES,
    FROZEN_VALIDATION_OPENED,
    MAX_FAVORABLE_RULES,
    NEXT_RCA,
    NEXT_STOP,
    OLD_CONFIRMATION_OPENED,
    PNL_OPTIMIZATION,
    R14_REPAIRED,
    RAW_DIRECTIONAL_FORBIDDEN,
    REFRACTORY_BARS,
    THRESHOLD_RETUNED,
    TREE_CRITERION,
    TREE_MAX_DEPTH,
    TREE_MIN_LEAF_ABS,
    TREE_MIN_LEAF_FRAC,
)
from research.native_direction_aligned_context_stack_v1.align import (
    aligned_ret,
    aligned_vwap_bps,
    break_distance_bps,
    close_location_strength,
    dir_of,
    sr_aligned,
)
from research.native_direction_aligned_context_stack_v1.analyze import build_report_body, decide, same_side_gap
from research.native_direction_aligned_context_stack_v1.isolation import OUT, write_overlap_n
from research.native_direction_aligned_context_stack_v1.publish import SHEET_ORDER
from research.native_direction_aligned_context_stack_v1.spec import source_sha256
from research.native_direction_aligned_context_stack_v1.tree import ZERO_FILL, min_leaf_n
from research.native_direction_aligned_context_stack_v1.walk import walk_events
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory, displacement
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def test_hashes_closed_paths_and_tree_settings():
    assert event_generator_sha256() == EXPECTED_EVENT_GENERATOR_SHA256
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert R14_REPAIRED is False
    assert THRESHOLD_RETUNED is False
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


def test_no_raw_unsigned_directional_in_model():
    for name in RAW_DIRECTIONAL_FORBIDDEN:
        assert name not in FEATURE_NAMES
    assert "aligned_r5" in FEATURE_NAMES
    assert "aligned_r15" in FEATURE_NAMES
    assert "aligned_prior5_ret" in FEATURE_NAMES
    assert "aligned_vwap_bps" in FEATURE_NAMES
    assert "ahead_sr_distance_atr" in FEATURE_NAMES
    assert "behind_sr_missing" in FEATURE_NAMES
    src = inspect.getsource(walk_events)
    assert '"r5":' not in src
    assert '"r15":' not in src
    assert "0.0040059" not in src
    assert "2.2981482" not in src


def test_dir_alignment_formulas():
    assert dir_of("BULLISH") == 1
    assert dir_of("BEARISH") == -1
    assert aligned_ret(0.01, "BULLISH") == 0.01
    assert aligned_ret(0.01, "BEARISH") == -0.01
    assert aligned_vwap_bps(101.0, 100.0, "BULLISH") == 100.0
    assert aligned_vwap_bps(101.0, 100.0, "BEARISH") == -100.0
    assert break_distance_bps(101.0, 100.0, 99.0, "BULLISH") > 0
    assert break_distance_bps(99.0, 101.0, 100.0, "BEARISH") > 0
    assert close_location_strength(10.0, 0.0, 10.0, "BULLISH") == 1.0
    assert close_location_strength(10.0, 0.0, 0.0, "BEARISH") == 1.0
    assert "ahead_sr_distance_atr" in ZERO_FILL


def test_ahead_behind_sr():
    snap = {
        "support_active": [{"lo": 90.0, "hi": 92.0}],
        "resistance_active": [{"lo": 110.0, "hi": 112.0}],
    }
    bull = sr_aligned(snap, close=100.0, atr=10.0, direction="BULLISH")
    bear = sr_aligned(snap, close=100.0, atr=10.0, direction="BEARISH")
    assert math.isclose(bull["ahead_sr_distance_atr"], 1.0)
    assert math.isclose(bull["behind_sr_distance_atr"], 0.8)
    assert bull["ahead_sr_missing"] == 0.0
    assert math.isclose(bear["ahead_sr_distance_atr"], 0.8)
    assert math.isclose(bear["behind_sr_distance_atr"], 1.0)
    empty = sr_aligned({"support_active": [], "resistance_active": []}, close=100.0, atr=10.0, direction="BULLISH")
    assert empty["ahead_sr_missing"] == 1.0
    assert empty["behind_sr_missing"] == 1.0
    assert not math.isfinite(empty["ahead_sr_distance_atr"])


def test_same_side_gap_blocks_composition():
    hit = [{"direction": "BULLISH", "y_p40": 1}, {"direction": "BULLISH", "y_p40": 1}]
    base = (
        [{"direction": "BULLISH", "y_p40": 1}] * 2
        + [{"direction": "BEARISH", "y_p40": 0}] * 8
    )
    g = same_side_gap(hit, base)
    assert g["same_side_gap"] == 0.0
    assert g["pooled_gap"] is not None and g["pooled_gap"] > 0


def test_no_strategy_or_indicator_catalog():
    src = inspect.getsource(walk_events) + inspect.getsource(decide) + inspect.getsource(build_report_body)
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert "RandomForest" not in src
    assert "XGB" not in src
    assert "RSI" not in src
    assert "MACD" not in src
    assert "Bollinger" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(walk_events)
    assert "0.80" not in inspect.getsource(walk_events)


def test_displacement_has_no_tv_gate():
    src = inspect.getsource(displacement)
    assert "0.80" not in src


def test_clock_prior_days_only():
    c = ClockHistory()
    rec = {"t": ["10:00"], "va": [1.0], "v": [1.0]}
    assert c.tv_pctl("7203", "10:00", 1.0) is None
    c.commit_day("7203", rec, [0])
    assert c.tv_pctl("7203", "10:00", 1.0) is None


def test_verdicts_and_sheets():
    assert CASE_FOUND == "DIRECTION_ALIGNED_CONTEXT_MECHANISM_FOUND_V1"
    assert CASE_PARTIAL == "DIRECTION_ALIGNED_CONTEXT_PARTIAL_MECHANISM_V1"
    assert CASE_NONE == "DIRECTION_ALIGNED_CONTEXT_NO_INCREMENTAL_RULE_V1"
    assert CASE_UNSTABLE == "DIRECTION_ALIGNED_CONTEXT_DISCOVERY_UNSTABLE_V1"
    assert NEXT_RCA == "DIRECTION_ALIGNED_CONTEXT_MECHANISM_RCA_V1"
    assert NEXT_STOP == "STOP_NATIVE_DIRECTION_ALIGNED_CONTEXT_STACK_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "Direction_Semantics" in SHEET_ORDER
    assert "SameSide_Base" in SHEET_ORDER
    assert "Matched_Control" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    d = decide({"unstable_in_discovery": True, "hard_mechanism_rule_ids": [], "partial_rule_ids": []})
    assert d["VERDICT"] == CASE_UNSTABLE
    assert d["NEXT"] == NEXT_STOP
    d2 = decide({"unstable_in_discovery": False, "hard_mechanism_rule_ids": ["R1"], "partial_rule_ids": []})
    assert d2["VERDICT"] == CASE_FOUND
    assert d2["NEXT"] == NEXT_RCA
    d3 = decide({"unstable_in_discovery": False, "hard_mechanism_rule_ids": [], "partial_rule_ids": []})
    assert d3["VERDICT"] == CASE_NONE
    overlap = write_overlap_n("", "")
    assert overlap == 0
    assert "native_direction_aligned_context_stack_v1" in str(OUT).replace("\\", "/")

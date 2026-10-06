"""Tests for V4 semantic spec clarification. No machine. No PnL. Parent spec and corrected SHA frozen."""
from __future__ import annotations

import inspect

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import CASE_READY as PARENT_SPEC_VERDICT
from research.pb1_v4_semantic_spec_clarification import (
    CASE_READY,
    E1_CAN_CREATE_TRADE_WITHOUT_THESIS_READY,
    E1_CAN_INVENT_SUPPORT_RESISTANCE,
    FAIL_EXTEND_MAX_BARS_IN_SPEC,
    MACHINE_IMPLEMENTED,
    ONE_M_CAN_CREATE_DIRECTION,
    ONE_M_CAN_CREATE_LOCATION_IDENTITY,
    ONE_M_CAN_CREATE_OPENING_DRIVE,
    ONE_M_CAN_CREATE_STOCK_SELECTION,
    PATH_EFFICIENCY_THRESHOLD_FROZEN,
    PRIMARY_SETUP_TIMEFRAME,
    TIMEFRAME_INTERPRETATION,
    V4_IS_1M_STRATEGY,
)
from research.pb1_v4_semantic_spec_clarification.analyze import decide
from research.pb1_v4_semantic_spec_clarification.isolation import CORRECTED_OUT, OUT, PARENT_SPEC_OUT, write_overlap_n
from research.pb1_v4_semantic_spec_clarification.specification import specification


def test_parent_and_corrected_frozen():
    assert PARENT_SPEC_VERDICT == "PB1_V4_SEMANTIC_SPEC_READY_V1"
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert MACHINE_IMPLEMENTED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_semantic_spec_clarification" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(PARENT_SPEC_OUT.resolve())
    assert str(OUT.resolve()) != str(CORRECTED_OUT.resolve())


def test_clarified_invariants():
    assert V4_IS_1M_STRATEGY is False
    assert PRIMARY_SETUP_TIMEFRAME == "5m"
    assert TIMEFRAME_INTERPRETATION == "B"
    assert ONE_M_CAN_CREATE_STOCK_SELECTION is False
    assert ONE_M_CAN_CREATE_DIRECTION is False
    assert ONE_M_CAN_CREATE_OPENING_DRIVE is False
    assert ONE_M_CAN_CREATE_LOCATION_IDENTITY is False
    assert E1_CAN_CREATE_TRADE_WITHOUT_THESIS_READY is False
    assert E1_CAN_INVENT_SUPPORT_RESISTANCE is False
    assert FAIL_EXTEND_MAX_BARS_IN_SPEC is False
    assert PATH_EFFICIENCY_THRESHOLD_FROZEN is False
    spec = specification()
    assert spec["overwrite_parent"] is False
    assert spec["parent_spec_preserved"] == "PB1_V4_SEMANTIC_SPEC_READY_V1"
    assert spec["FAILED_OPEN"]["wide_doji_or_range_rejection_may_be_valid_seed"] is True
    assert spec["FAILED_OPEN"]["FAIL_EXTEND_MAX_BARS_in_semantic_spec"] is False
    assert spec["opening_drive_seed"]["is_permanent_setup_eligibility"] is False
    assert spec["continued_directional_intent"]["explicitly_part_of_spec"] is True
    assert spec["normal_opening_scale"]["same_clock_opening_baselines_required"] is True
    assert spec["normal_opening_scale"]["first_bar_only_normalization_of_all_three_bars"] is False
    assert spec["location_interaction"]["first_unsuccessful_observation_does_not_automatically_kill"] is True
    assert spec["five_m_continuation"]["requires_huge_completed_5m_candle"] is False
    assert spec["E1"]["can_invent_support_resistance"] is False
    assert spec["timeframe_contract"]["adopted_interpretation"] == "B"
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    d = decide(True)
    assert d["VERDICT"] == CASE_READY
    assert d["machine_implemented"] is False
    assert decide(False)["VERDICT"] == "PB1_V4_CLARIFIED_SPEC_BIND_FAILED_V1"

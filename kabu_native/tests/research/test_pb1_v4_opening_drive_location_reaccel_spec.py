"""Tests for V4 semantic spec. No machine. No PnL. V3.2 frozen."""
from __future__ import annotations

import inspect

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_opening_drive_location_reaccel_spec import (
    INVALID_OPENING_STATES,
    NUMERIC_1M_THRESHOLDS_FROZEN,
    ONE_M_CAN_CREATE_ELIGIBILITY,
    PARENT_V32_SHA,
    VALID_OPENING_STATES,
    V4_IS_1M_STRATEGY,
    V4_MACHINE_IMPLEMENTED,
)
from research.pb1_v4_opening_drive_location_reaccel_spec.analyze import decide
from research.pb1_v4_opening_drive_location_reaccel_spec.isolation import OUT, RCA_OUT, V32_OUT, write_overlap_n
from research.pb1_v4_opening_drive_location_reaccel_spec.specification import specification


def test_v32_frozen_and_spec_invariants():
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert V4_IS_1M_STRATEGY is False
    assert ONE_M_CAN_CREATE_ELIGIBILITY is False
    assert NUMERIC_1M_THRESHOLDS_FROZEN is False
    assert V4_MACHINE_IMPLEMENTED is False
    assert VALID_OPENING_STATES == ("TRUE_OPENING_DRIVE", "FAILED_OPEN_THEN_REAL_DRIVE")
    assert "MICRO_OR_LEAK" in INVALID_OPENING_STATES
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_opening_drive_location_reaccel_spec" in str(OUT).replace("\\", "/")
    assert "pb1_v3_2_face_failure_rca" in str(RCA_OUT).replace("\\", "/")
    assert "pb1_v3_2_opening_drive_and_reacceleration_semantics" in str(V32_OUT).replace("\\", "/")
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    spec = specification()
    assert spec["v4_is_1m_strategy"] is False
    assert spec["one_m_execution"]["can_create_eligibility_without_5m"] is False
    assert spec["one_m_execution"]["numeric_thresholds_frozen"] is False
    forbidden = " ".join(spec["one_m_execution"]["rca_medians_are_diagnostic_only"]["forbidden_examples"])
    assert "3bar >= 2.0" in forbidden
    d = decide(True)
    assert d["VERDICT"] == "PB1_V4_SEMANTIC_SPEC_READY_V1"
    assert d["v4_machine_implemented"] is False
    assert decide(False)["VERDICT"] == "PB1_V4_SPEC_BIND_FAILED_V1"

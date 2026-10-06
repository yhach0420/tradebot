"""Tests for SUPPORT_RESISTANCE_TEST_DESIGN_AUDIT_V1. No Confirmation. No Frozen Validation. No PnL."""
from __future__ import annotations

from research.support_resistance_test_design_audit_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    FROZEN_VALIDATION_OPENED,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    SAMPLE_N,
    TRANSITION_N,
    ZONE_WIDTH_SELECTED_BY_PNL,
)
from research.support_resistance_test_design_audit_v1.analyze import decide
from research.support_resistance_test_design_audit_v1.design import retest_semantics_from_source
from research.multi_touch_daily_zone_1m_price_action_v1.machine import new_zone_state, step_zone
from research.support_resistance_test_design_audit_v1.isolation import OUT, ZONE_OUT, write_overlap_n
from research.support_resistance_test_design_audit_v1.publish import SHEET_ORDER
from research.support_resistance_test_design_audit_v1.spec import SOURCE_FILES, source_sha256


def test_parent_and_safety() -> None:
    assert PARENT_VERDICT == "MULTI_TOUCH_ZONE_NO_INCREMENTAL_INFORMATION_V1"
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert ZONE_WIDTH_SELECTED_BY_PNL is False
    assert SAMPLE_N >= 200
    assert TRANSITION_N >= 100


def test_retest_not_exclusive() -> None:
    s = retest_semantics_from_source()
    assert s["retest_and_retest_hold_mutually_exclusive"] is False
    assert s["current_machine_requires_clearance"] is False
    assert s["lingering_near_zone_counted_as_hold"] is True


def test_decide_multiple_deficiencies() -> None:
    d = decide(bind_ok=True, face_valid=False, overcounts=True, gate_misaligned=True, density_implausible=True)
    assert d["VERDICT"] == CASE_D
    assert d["old_no_info_still_justified"] is False


def test_decide_e_only_if_design_valid() -> None:
    d = decide(bind_ok=True, face_valid=True, overcounts=False, gate_misaligned=False, density_implausible=False)
    assert d["VERDICT"] == CASE_E
    a = decide(bind_ok=True, face_valid=False, overcounts=False, gate_misaligned=False, density_implausible=True)
    assert a["VERDICT"] == CASE_A
    b = decide(bind_ok=True, face_valid=True, overcounts=True, gate_misaligned=False, density_implausible=False)
    assert b["VERDICT"] == CASE_B
    c = decide(bind_ok=True, face_valid=True, overcounts=False, gate_misaligned=True, density_implausible=False)
    assert c["VERDICT"] == CASE_C


def test_sheets_and_source() -> None:
    assert SHEET_ORDER[0] == "Binding"
    assert "Random_Chart_Sample_200" in SHEET_ORDER
    assert "Retest_Semantics" in SHEET_ORDER
    assert "State_Transition_Samples" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER[-1]
    assert len(SHEET_ORDER) == 14
    sha = source_sha256()
    assert len(sha) == 64
    assert "__init__.py" in SOURCE_FILES
    assert OUT.name == "support_resistance_test_design_audit_v1"
    assert ZONE_OUT.name == "multi_touch_daily_zone_1m_price_action_v1"
    assert write_overlap_n("", "") == 0


def test_retest_hold_co_emitted_without_clearance() -> None:
    z = {
        "zone_id": "t:RESISTANCE:20240917:100",
        "role": "RESISTANCE",
        "center": 100.0,
        "lo": 99.0,
        "hi": 101.0,
        "touch_count": 2,
        "distinct_touch_days": 2,
        "touch_bucket": "2",
        "ZONE_ACTIVATED_AT": "20240918",
        "control_single_touch": False,
    }
    st = new_zone_state(z)
    # Close crosses hi while the bar still overlaps the zone: break and "retest hold" on the same minute.
    ev1 = step_zone(st, c=102.0, h=102.5, l=100.5, prev_c=100.5, tm=540, feature_bar="09:00")
    kinds1 = [e["event_kind"] for e in ev1]
    assert "BREAK_ABOVE_ZONE" in kinds1
    assert "RETEST_ZONE" in kinds1
    assert "RETEST_HOLD" in kinds1
    # Two closes above hi → flip; flipped stays true so later in-zone bars keep emitting hold.
    step_zone(st, c=102.4, h=102.6, l=102.0, prev_c=102.0, tm=541, feature_bar="09:01")
    step_zone(st, c=102.5, h=102.7, l=102.1, prev_c=102.4, tm=552, feature_bar="09:12")
    ev4 = step_zone(st, c=100.2, h=101.0, l=99.5, prev_c=102.5, tm=563, feature_bar="09:23")
    kinds4 = [e["event_kind"] for e in ev4]
    assert "RETEST_HOLD" in kinds4
    assert st.get("flipped") is True

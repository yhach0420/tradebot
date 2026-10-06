"""Tests for matched-separation-not-a-strategy. No Confirmation. No Frozen Validation. No PnL."""
from __future__ import annotations

import inspect

from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256
from research.support_resistance_matched_separation_not_a_strategy_v1 import (
    CASE_BOTH,
    CASE_CONSUMED,
    CONTINUE_MECHANISMS,
    DETECTOR_RETUNED,
    DIAGNOSTIC_ONLY,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_VALIDATION_OPENED,
    MATCHABILITY_USED_AS_ENTRY_FILTER,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    STRATEGY_SELECTED,
    A_VS_C_SELECTED_BY_EFFECT_SIZE,
)
from research.support_resistance_matched_separation_not_a_strategy_v1.analyze import decide
from research.support_resistance_matched_separation_not_a_strategy_v1.executable import a2_confirmed
from research.support_resistance_matched_separation_not_a_strategy_v1.isolation import MATCHED_OUT, OUT, REBUILD_OUT, write_overlap_n
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i
from research.support_resistance_matched_separation_not_a_strategy_v1.propensity import COV, overlap_weighted
from research.support_resistance_matched_separation_not_a_strategy_v1.publish import SHEET_ORDER
from research.support_resistance_matched_separation_not_a_strategy_v1.spec import source_sha256


def test_freeze_and_safety() -> None:
    assert PARENT_VERDICT == "FACE_VALID_SR_MATCHED_PATH_SEPARATION_FOUND_V1"
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert DETECTOR_RETUNED is False
    assert STRATEGY_SELECTED is False
    assert MATCHABILITY_USED_AS_ENTRY_FILTER is False
    assert A_VS_C_SELECTED_BY_EFFECT_SIZE is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert CONTINUE_MECHANISMS == ("A", "C")
    assert DIAGNOSTIC_ONLY == ("B", "D")
    assert write_overlap_n("", "") == 0
    assert OUT.name == "support_resistance_matched_separation_not_a_strategy_v1"
    assert MATCHED_OUT.name == "support_resistance_first_interaction_matched_causal_test_v1"
    assert REBUILD_OUT.name == "support_resistance_face_valid_first_interaction_rebuild_v1"
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "A1_Passive" in SHEET_ORDER
    assert "Exit_Architecture" in SHEET_ORDER
    assert len(source_sha256()) == 64


def test_no_complex_propensity() -> None:
    src = inspect.getsource(overlap_weighted)
    for banned in ("XGBClassifier", "RandomForestClassifier", "MLPClassifier"):
        assert banned not in src
    assert "p20" not in COV
    assert "mfe" not in COV
    assert "end" not in COV


def test_a2_entry_is_next_bar() -> None:
    rec = {
        "n": 4,
        "t": ["09:01", "09:02", "09:03", "09:04"],
        "idx": {"09:01": 0, "09:02": 1, "09:03": 2, "09:04": 3},
        "o": [100.0, 99.5, 99.2, 99.0],
        "h": [100.2, 99.6, 99.4, 99.2],
        "l": [99.8, 98.8, 98.9, 98.7],
        "c": [100.0, 99.0, 99.1, 98.9],
    }
    ep = {
        "as_resistance": True,
        "lo": 99.0,
        "hi": 101.0,
        "first_test_time": "09:01",
        "rejection": True,
        "break": False,
        "rejection_time": "09:02",
    }
    got = a2_confirmed(ep, rec)
    assert got["same_bar_entry"] is False
    assert got["entry_t"] == "09:03"
    assert next_entry_i(rec, 1) == 2


def test_decide_does_not_pick_larger_effect() -> None:
    a = {"proceed": True, "inference_fail": False, "transport_fail": False, "consumed": False}
    c = {"proceed": True, "inference_fail": False, "transport_fail": False, "consumed": False}
    d = decide(bind_ok=True, freeze_ok=True, a=a, c=c)
    assert d["VERDICT"] == CASE_BOTH
    assert d["selected_by_effect_size"] is False
    none = decide(
        bind_ok=True,
        freeze_ok=True,
        a={"proceed": False, "inference_fail": False, "transport_fail": False, "consumed": True},
        c={"proceed": False, "inference_fail": False, "transport_fail": False, "consumed": True},
    )
    assert none["VERDICT"] == CASE_CONSUMED

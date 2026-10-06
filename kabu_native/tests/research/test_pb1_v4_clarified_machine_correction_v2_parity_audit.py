"""Tests for Correction V2 parity audit. Frozen hashes must not change. No PnL."""
from __future__ import annotations

import inspect

from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v2_parity_audit import (
    ANY_CODE_CHANGED,
    CASE_FAIL,
    CASE_PASS,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_PARENT_MACHINE_SHA256,
    EXPECTED_SPEC_SHA256,
    NEXT_PREP,
    NEXT_RCA,
)
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.analyze import decide
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.hidden1m import recompute_hidden_1m
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.isolation import CORRECTION_OUT, CORRECTION_SRC, OUT, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.spec import SOURCE_FILES, source_sha256
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def test_frozen_hashes_and_isolation():
    assert spec_sha256() == EXPECTED_SPEC_SHA256
    assert correction_v2_sha256() == EXPECTED_CORRECTION_V2_SHA256
    assert parent_machine_sha256() == EXPECTED_PARENT_MACHINE_SHA256
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert ANY_CODE_CHANGED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v2_parity_audit" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(CORRECTION_OUT.resolve())
    root = CORRECTION_SRC.parent / "pb1_v4_clarified_machine_correction_v2_parity_audit"
    for name in SOURCE_FILES:
        assert (root / name).is_file(), name
    assert len(source_sha256()) == 64


def test_hidden_1m_masks_execution_and_counts_mismatch():
    walked = {
        "ok": True,
        "setups": [
            {
                "symbol": "3382",
                "date": "20241004",
                "direction": "bear",
                "DIR": -1,
                "opening_drive_id": "D1",
                "location_id": "L1",
                "thesis_id": "T1",
                "hidden_1m_snapshot": {
                    "stock": "3382",
                    "direction": "bear",
                    "opening_drive_id": "D1",
                    "location_id": "L1",
                    "thesis_id": "T1",
                    "one_m_hidden": True,
                },
            }
        ],
        "hidden_1m_checks": [],
        "HIDDEN_1M_THESIS_PARITY": True,
        "counts": {
            "1M_created_location_n": 0,
            "1M_changed_direction_n": 0,
            "1M_changed_opening_drive_n": 0,
            "THESIS_REVIVED_BY_EXECUTION_n": 0,
        },
        "e1_events": [{"symbol": "3382", "should_be_ignored": True}],
    }
    out = recompute_hidden_1m(walked=walked)
    assert out["recomputed"] is True
    assert out["e1_events_used"] is False
    assert out["mismatch_n"] == 0
    walked["setups"][0]["hidden_1m_snapshot"]["location_id"] = "OTHER"
    out2 = recompute_hidden_1m(walked=walked)
    assert out2["mismatch_n"] == 1


def _base_kwargs() -> dict:
    return {
        "bind_ok": True,
        "hashes_unchanged": True,
        "fingerprint_drift": False,
        "hidden_mismatch_n": 0,
        "one_m_created": {"seed": 0, "active": 0, "location": 0, "changed_direction": 0, "revived_thesis": 0},
        "invariant_violations": 0,
        "failed_open_pos_ok": True,
        "failed_open_neg_ok": True,
        "micro_revived": False,
        "one_bar_concept": "yes",
        "two_sided_over_reject_n": 0,
        "two_sided_7741_ok": True,
        "thesis_freeze": False,
        "a2_leak_n": 0,
        "a3_minted_n": 0,
    }


def test_decide_fail_on_micro_and_two_sided_and_pass_when_clean():
    fail = decide(**{**_base_kwargs(), "micro_revived": True, "two_sided_over_reject_n": 4, "one_bar_concept": "partially"})
    assert fail["VERDICT"] == CASE_FAIL
    assert fail["NEXT"] == NEXT_RCA
    ok = decide(**_base_kwargs())
    assert ok["VERDICT"] == CASE_PASS
    assert ok["NEXT"] == NEXT_PREP
    src = inspect.getsource(decide)
    assert "PnL" not in src
    assert "MFE" not in src
    assert "accuracy_is_not_the_criterion" in src

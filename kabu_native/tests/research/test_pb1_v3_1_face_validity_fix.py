"""Tests for PB1 V3.1 face-validity fix. No PnL. V2 and V3 frozen."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_opening_range_continuation_face_valid_v2.impulse import opening_path
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix import (
    DRIVE_NET_OR_FRAC,
    MEANINGFUL_LEAVE_NOISE_MULT,
    MEANINGFUL_R_NOISE_MULT,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
)
from research.pb1_v3_1_face_validity_fix.analyze import decide
from research.pb1_v3_1_face_validity_fix.drive import classify_drive, opening_impulse_lost
from research.pb1_v3_1_face_validity_fix.isolation import OUT, V2_OUT, V3_OUT, write_overlap_n
from research.pb1_v3_1_face_validity_fix.machine import step_side
from research.pb1_v3_1_face_validity_fix.spec import source_sha256


def test_parents_frozen_and_constants_not_searched():
    assert v2_machine_sha256() == PARENT_V2_SHA
    assert v3_machine_sha256() == PARENT_V3_SHA
    assert PARENT_V3_SHA == "4348b941f9eb0a827db743041752c49f91be8cd9f92a1920c4ffd7c6c0dc8f89"
    assert MEANINGFUL_R_NOISE_MULT == 1.0
    assert MEANINGFUL_LEAVE_NOISE_MULT == 1.0
    assert DRIVE_NET_OR_FRAC == 0.50
    assert source_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_v3_1_face_validity_fix" in str(OUT).replace("\\", "/")
    assert "pb1_playbook_redesign_v3" in str(V3_OUT).replace("\\", "/")
    assert "pb1_opening_range_continuation_face_valid_v2" in str(V2_OUT).replace("\\", "/")
    src = inspect.getsource(step_side) + inspect.getsource(decide) + inspect.getsource(classify_drive)
    assert "profit_factor" not in src.lower()
    assert "09:30" not in src
    assert "09:45" not in src
    assert "retest <= 5" not in src.lower()


def test_drive_v2_requires_half_or_and_mirrors():
    path = {
        "ok": True,
        "open_0900": 100.0,
        "close_0904": 102.0,
        "close_0914": 110.0,
        "or_mid": 105.0,
        "or_range": 20.0,
        "mfe_up": 12.0,
        "mae_dn": 2.0,
        "net_or15": 10.0,
        "net_5m": 2.0,
    }
    d = classify_drive(path)
    assert d["state"] == "CLEAN_OPENING_DRIVE_V2"
    assert d["DIR"] == 1
    path["net_or15"] = 9.0
    assert classify_drive(path)["state"] == "NON_DIRECTIONAL_OPEN"
    bear = {
        "ok": True,
        "open_0900": 100.0,
        "close_0904": 98.0,
        "close_0914": 90.0,
        "or_mid": 95.0,
        "or_range": 20.0,
        "mfe_up": 2.0,
        "mae_dn": 12.0,
        "net_or15": -10.0,
        "net_5m": -2.0,
    }
    b = classify_drive(bear)
    assert b["state"] == "CLEAN_OPENING_DRIVE_V2" and b["DIR"] == -1
    assert opening_impulse_lost(sign=1, close=104.0, or_high=110.0, or_low=100.0) is True
    assert opening_impulse_lost(sign=1, close=106.0, or_high=110.0, or_low=100.0) is False
    _ = opening_path


def test_decide_no_independent_sample():
    walked = {"failed_push_leak_n": 0, "risk_invalid_n": 0, "micro_structure_leak_n": 0, "counts": {"setup_n": 10}}
    d = decide(True, True, {"reviewed_n": 0, "actual_manual_blinded_review": False}, walked, {"unseen_candidate_n": 0, "independent_sample_n": 0})
    assert d["INDEPENDENT_FACE_VERIFY_NOT_AVAILABLE_IN_DISCOVERY"] is True
    assert d["FACE_VALID"] is False
    assert d["NOT_INDEPENDENT_FACE_VALIDATION"] is True
    assert d["VERDICT"] == "PB1_V3_1_INDEPENDENT_FACE_VERIFY_NOT_AVAILABLE_IN_DISCOVERY_V1"

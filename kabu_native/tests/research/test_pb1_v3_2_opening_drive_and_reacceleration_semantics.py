"""Tests for PB1 V3.2. No PnL. Parents frozen."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import (
    OR_CLOSE_HALF,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    REACCEL_CLOSE_LOC,
    RECLAIM_1N1M_FROM_PROFIT,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.analyze import decide
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.drive import classify_drive
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.isolation import OUT, V31_OUT, write_overlap_n
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.machine import step_side
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.reaccel import classify_reacceleration


def test_parents_frozen_and_no_profit_search():
    assert v2_machine_sha256() == PARENT_V2_SHA
    assert v3_machine_sha256() == PARENT_V3_SHA
    assert v31_machine_sha256() == PARENT_V31_SHA
    assert PARENT_V31_SHA == "24089d94b2610eb8eaf19160d503b6477347f970a27650b6a04282884f436c65"
    assert OR_CLOSE_HALF == 0.50
    assert REACCEL_CLOSE_LOC == 0.50
    assert RECLAIM_1N1M_FROM_PROFIT is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v3_2" in str(OUT).replace("\\", "/")
    assert "pb1_v3_1_face_validity_fix" in str(V31_OUT).replace("\\", "/")
    src = inspect.getsource(step_side) + inspect.getsource(decide) + inspect.getsource(classify_drive)
    assert "profit_factor" not in src.lower()
    assert "09:30" not in src
    assert "09:45" not in src


def test_drive_v3_allows_early_reversal_bear_dump():
    path = {
        "ok": True,
        "open_0900": 100.0,
        "close_0904": 102.0,
        "close_0914": 91.0,
        "or_mid": 96.0,
        "or_range": 20.0,
        "or_high": 106.0,
        "or_low": 86.0,
        "mfe_up": 6.0,
        "mae_dn": 14.0,
        "net_or15": -9.0,
        "net_5m": 2.0,
    }
    d = classify_drive(path)
    assert d["state"] == "CLEAN_OPENING_DRIVE_V3"
    assert d["DIR"] == -1
    assert d["auction"] == "EARLY_REVERSAL_THEN_DOMINANT_DRIVE"
    assert d["session_open_net_required"] is False


def test_drive_v3_rejects_two_sided_mid():
    path = {
        "ok": True,
        "open_0900": 100.0,
        "close_0904": 100.0,
        "close_0914": 100.0,
        "or_mid": 100.0,
        "or_range": 20.0,
        "or_high": 110.0,
        "or_low": 90.0,
        "mfe_up": 10.0,
        "mae_dn": 10.0,
        "net_or15": 0.0,
        "net_5m": 0.0,
    }
    d = classify_drive(path)
    assert d["state"] == "NON_DIRECTIONAL_OPEN"
    assert d["auction"] == "TWO_SIDED_OPEN"


def test_reaccel_requires_expansion_and_body():
    ok = classify_reacceleration(
        sign=1,
        open_px=100.0,
        high=103.0,
        low=100.0,
        close=102.5,
        prev_close=100.2,
        retest_high=101.0,
        retest_low=99.0,
        retest_range=1.5,
        n1m=2.0,
    )
    assert ok["ok"] is True
    tiny = classify_reacceleration(
        sign=1,
        open_px=101.05,
        high=101.2,
        low=100.9,
        close=101.15,
        prev_close=101.0,
        retest_high=101.0,
        retest_low=100.0,
        retest_range=2.0,
        n1m=2.0,
    )
    assert tiny["ok"] is False
    d = decide(True, True, {"independent_face_sample_n": 0})
    assert d["FACE_VALID"] is False
    assert d["NEXT"] == "WAIT_FOR_PROSPECTIVE_UNSEEN_FACE_VERIFY_V1"

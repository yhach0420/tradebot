"""Tests for PB1 V3 redesign. No PnL. V2 frozen. 1R not searched."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3 import (
    ACCEPTANCE_FAIL_CLOSES,
    ARCHIVED_TRIGGER,
    PARENT_V2_SHA,
    PRIMARY_TRIGGER,
    STRUCTURAL_ROUTE_R_MULT,
)
from research.pb1_playbook_redesign_v3.analyze import decide
from research.pb1_playbook_redesign_v3.classify import HUMAN_LABELS
from research.pb1_playbook_redesign_v3.isolation import OUT, V2_OUT, write_overlap_n
from research.pb1_playbook_redesign_v3.machine import is_failed_push, is_reclaim
from research.pb1_playbook_redesign_v3.spec import source_sha256
from research.pb1_playbook_redesign_v3.structure_route import planned_r, structural_route
from research.pb1_structure_and_symbol_context_rca_v1.structure import level_zone


def test_v2_frozen_and_failed_push_not_pb1():
    assert v2_machine_sha256() == PARENT_V2_SHA
    assert PARENT_V2_SHA == "3ebe0220fd9cb1e5749e39833fa1bab0146759cce5dc9daaacf0e3b3d3daf3fd"
    assert PRIMARY_TRIGGER == "RECLAIM_RETEST_MICRO_HIGH"
    assert ARCHIVED_TRIGGER == "FAILED_PUSH_THEN_CLOSE_BACK"
    assert STRUCTURAL_ROUTE_R_MULT == 1.0
    assert ACCEPTANCE_FAIL_CLOSES == 2
    assert source_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_playbook_redesign_v3" in str(OUT).replace("\\", "/")
    assert "pb1_opening_range_continuation_face_valid_v2" in str(V2_OUT).replace("\\", "/")
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert is_reclaim(sign=1, close=101.1, retest_high=101.0, retest_low=99.0) is True
    assert is_failed_push(sign=1, high=100.5, low=99.5, close=100.2, boundary=100.0) is True


def test_planned_r_and_route_1r_not_searched():
    r = planned_r(sign=1, trigger_close=1000.0, retest_high=1002.0, retest_low=990.0)
    assert r == 10.0
    z = level_zone("PDH", "RESISTANCE", 1004.5, 10.0)
    assert z is not None
    blocked = structural_route(sign=1, trigger_close=1000.0, r_px=10.0, zones=[z])
    assert blocked["status"] == "STRUCTURALLY_BLOCKED"
    assert blocked["one_r_searched"] is False
    z2 = level_zone("PDH", "RESISTANCE", 1020.0, 10.0)
    clear = structural_route(sign=1, trigger_close=1000.0, r_px=10.0, zones=[z2])
    assert clear["status"] == "STRUCTURAL_ROUTE_CLEAR"
    invalid = structural_route(sign=1, trigger_close=1000.0, r_px=0.0, zones=[])
    assert invalid["status"] == "RETEST_EXTREME_INVALID"
    from research.pb1_playbook_redesign_v3 import STRUCTURAL_ROUTE_R_MULT as M

    assert M == 1.0


def test_decide_requires_manual_review_not_self_match():
    walked = {"failed_push_leak_n": 0, "risk_invalid_n": 0}
    no_labels = decide(True, True, {"reviewed_n": 0, "actual_manual_blinded_review": False, "clear_share": 1.0}, walked)
    assert no_labels["FACE_VALID"] is False
    ok = decide(
        True,
        True,
        {
            "reviewed_n": 96,
            "actual_manual_blinded_review": True,
            "clear_share": 0.70,
            "NOT_CONTINUATION": 5,
            "structurally_blocked_leak_share": 0.05,
        },
        walked,
    )
    assert ok["FACE_VALID"] is True
    assert ok["NEXT"] == "PB1_V3_CAUSAL_PATH_TEST_V1"
    leak = decide(
        True,
        True,
        {
            "reviewed_n": 96,
            "actual_manual_blinded_review": True,
            "clear_share": 0.70,
            "NOT_CONTINUATION": 5,
            "structurally_blocked_leak_share": 0.05,
        },
        {"failed_push_leak_n": 1, "risk_invalid_n": 0},
    )
    assert leak["FACE_VALID"] is False


def test_human_labels_cover_face_sample_not_self_match():
    assert set(HUMAN_LABELS) == set(range(1, 68))
    for lab in HUMAN_LABELS.values():
        assert lab["pattern"] in {"CLEAR_CONTINUATION", "QUESTIONABLE", "NOT_CONTINUATION"}
        assert lab["location"] in {"CLEAR_ROUTE", "QUESTIONABLE_ROUTE", "STRUCTURALLY_BLOCKED"}
        assert lab["retest_lab"] in {"FRESH_RETEST", "EXHAUSTED_RETEST", "AMBIGUOUS"}
        assert lab["trigger_lab"] in {"VALID_RECLAIM", "WEAK_RECLAIM", "INVALID_TRIGGER"}
        assert lab["future_used"] is False

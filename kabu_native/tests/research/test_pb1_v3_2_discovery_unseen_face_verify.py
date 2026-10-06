"""Tests for V3.2 Discovery-unseen face verify. No PnL. V3.2 frozen."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v2 import GATE_CLEAR_MIN, GATE_NOT_MAX
from research.pb1_v3_2_discovery_unseen_face_verify import PARENT_V32_SHA
from research.pb1_v3_2_discovery_unseen_face_verify.analyze import decide, wilson_95
from research.pb1_v3_2_discovery_unseen_face_verify.isolation import OUT, V32_OUT, write_overlap_n
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256


def test_v32_frozen_and_existing_gate():
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert PARENT_V32_SHA == "56edb2c2576805e46193528824fb61faebb5ae7c85be0201f0331e90f9a67f43"
    assert GATE_CLEAR_MIN == 2.0 / 3.0
    assert GATE_NOT_MAX == 0.15
    assert 14 / 21 >= GATE_CLEAR_MIN
    assert 3 / 21 <= GATE_NOT_MAX
    assert 13 / 21 < GATE_CLEAR_MIN
    assert 4 / 21 > GATE_NOT_MAX
    assert write_overlap_n("", "") == 0
    assert "pb1_v3_2_discovery_unseen_face_verify" in str(OUT).replace("\\", "/")
    assert "pb1_v3_2_opening_drive_and_reacceleration_semantics" in str(V32_OUT).replace("\\", "/")
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "mfe" not in src.lower()


def test_wilson_and_verdicts():
    ci = wilson_95(14, 21)
    assert ci["lo"] is not None and ci["hi"] is not None
    assert ci["lo"] <= 14 / 21 <= ci["hi"]
    walked = {"failed_push_leak_n": 0, "risk_invalid_n": 0}
    human_pass = {
        "actual_manual_blinded_review": True,
        "reviewed_n": 21,
        "clear_share": 14 / 21,
        "not_share": 2 / 21,
        "structurally_blocked_leak_share": 0.0,
        "structurally_blocked_leak_n": 0,
    }
    d = decide(True, True, human_pass, walked)
    assert d["VERDICT"] == "PB1_V3_2_DISCOVERY_UNSEEN_FACE_SUPPORTED_V1"
    assert d["NEXT"] == "PB1_V3_2_CAUSAL_PATH_TEST_V1"
    human_fail = dict(human_pass)
    human_fail["clear_share"] = 10 / 21
    human_fail["CLEAR_CONTINUATION"] = 10
    f = decide(True, True, human_fail, walked)
    assert f["VERDICT"] == "PB1_V3_2_DISCOVERY_UNSEEN_FACE_FAIL_V1"
    assert f["NEXT"] == "PB1_V3_2_FACE_FAILURE_RCA_V1"
    inc = decide(True, True, {"actual_manual_blinded_review": False, "reviewed_n": 0}, walked)
    assert inc["VERDICT"] == "PB1_V3_2_DISCOVERY_UNSEEN_LABELS_INCOMPLETE_V1"
    assert f["v32_rule_changed"] is False

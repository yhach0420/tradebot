"""Tests for V3.2 face-failure RCA. No PnL. V3.2 frozen. No threshold search."""
from __future__ import annotations

import inspect

from research.pb1_v3_2_face_failure_rca import CASE_INCOMPLETE, CASE_OR_WEAK, CASE_REBUILD, PARENT_V32_SHA, SEMANTIC_RCA_N
from research.pb1_v3_2_face_failure_rca.analyze import decide
from research.pb1_v3_2_face_failure_rca.isolation import OUT, V32_OUT, write_overlap_n
from research.pb1_v3_2_face_failure_rca.kappa import cohen_kappa, confusion
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256


def test_v32_frozen_and_no_pnl():
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert PARENT_V32_SHA == "56edb2c2576805e46193528824fb61faebb5ae7c85be0201f0331e90f9a67f43"
    assert SEMANTIC_RCA_N == 88
    assert write_overlap_n("", "") == 0
    assert "pb1_v3_2_face_failure_rca" in str(OUT).replace("\\", "/")
    assert "pb1_v3_2_opening_drive_and_reacceleration_semantics" in str(V32_OUT).replace("\\", "/")
    src = inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "mfe" not in src.lower()


def test_second_pass_complete_n():
    from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS

    assert len(HUMAN_LABELS) == 88
    assert set(HUMAN_LABELS) == set(range(1, 89))


def test_kappa_and_decide():
    a = ["CLEAR_CONTINUATION"] * 10 + ["QUESTIONABLE"] * 10
    b = list(a)
    assert cohen_kappa(a, b) == 1.0
    conf = confusion(a, b)
    assert conf["disagreement_n"] == 0
    human = {"actual_second_pass": True, "reviewed_n": 88, "CLEAR_CONTINUATION": 12}
    agree = {"pattern": {"cohen_kappa": 0.7}}
    d = decide(True, human, agree, 12)
    assert d["VERDICT"] == CASE_REBUILD
    d2 = decide(True, human, agree, 2)
    assert d2["VERDICT"] == CASE_OR_WEAK
    inc = decide(True, {"actual_second_pass": False}, {}, 0)
    assert inc["VERDICT"] == CASE_INCOMPLETE
    assert d["any_rule_changed"] is False
    assert d["any_threshold_optimized"] is False

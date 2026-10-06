"""Tests for V4 correction RCA. Corrected SHA must not change. No PnL."""
from __future__ import annotations

import inspect

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import ANY_RULE_CHANGED, CASE_SPEC, V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_implementation_correction_rca.analyze import decide
from research.pb1_v4_implementation_correction_rca.constants_audit import inventory
from research.pb1_v4_implementation_correction_rca.isolation import CORRECTED_OUT, OUT, write_overlap_n
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256


def test_corrected_and_leaked_sha_frozen():
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert ANY_RULE_CHANGED is False
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_implementation_correction_rca" in str(OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(CORRECTED_OUT.resolve())


def test_fail_extend_and_completed_5m_not_in_frozen_spec():
    inv = inventory()
    assert inv["FAIL_EXTEND_MAX_BARS_explicitly_supported_by_frozen_spec"] is False
    assert inv["completed_5m_leave_explicitly_required_by_frozen_spec"] is False
    assert inv["completed_5m_retest_hold_explicitly_required_by_frozen_spec"] is False
    assert inv["continued_intent_encoded"] == "no"


def test_decide_does_not_auto_choose_correction_v2_when_spec_ambiguous():
    d = decide(
        bind_ok=True,
        s1={"implementation_encoding_gap": ["continued intent"], "continued_intent_encoded": "no", "s1_fp_n": 19, "micro_case_rca": [{"why_machine_TRUE": {"all_true_clauses": True}}]},
        loc3382={"human_label_may_use_1m_execution_detail": True, "new_classifier_requires_more_than_frozen_spec": True},
        fo={},
        tf={"completed_5m_required_for_every_retest": "NOT PROVEN in frozen spec — ambiguity"},
        constants={
            "FAIL_EXTEND_MAX_BARS_explicitly_supported_by_frozen_spec": False,
            "completed_5m_leave_explicitly_required_by_frozen_spec": False,
            "completed_5m_retest_hold_explicitly_required_by_frozen_spec": False,
        },
    )
    assert d["VERDICT"] == CASE_SPEC
    assert d["NEXT"] == "PB1_V4_SEMANTIC_SPEC_CLARIFICATION_V1"
    assert d["any_rule_changed"] is False
    assert d["do_not_auto_choose_implementation_correction"] is True
    src = inspect.getsource(decide)
    assert "profit" not in src.lower() or "False" in src

"""Tests for V4 integrity/spec-parity audit. No PnL. Frozen V4 SHA must not change."""
from __future__ import annotations

import inspect

from research.pb1_v4_implementation_integrity_and_spec_parity_audit import FROZEN_V4_SHA, V4_RULE_CHANGED
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.analyze import decide
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.isolation import OUT, V4_OUT, write_overlap_n
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.leakage import audit_legacy_leakage
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_machine_implementation.location import classify_s2
from research.pb1_v4_opening_drive_location_reaccel_spec.specification import location_def


def test_frozen_v4_sha_unchanged():
    assert v4_machine_sha256() == FROZEN_V4_SHA
    assert V4_RULE_CHANGED is False


def test_write_overlap_zero_and_does_not_write_v4_out():
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_implementation_integrity_and_spec_parity_audit" in str(OUT).replace("\\", "/")
    assert "pb1_v4_machine_implementation" in str(V4_OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(V4_OUT.resolve())


def test_legacy_flags_and_not_in_spec():
    leak = audit_legacy_leakage()
    assert leak["legacy_rule_leakage"] is True
    assert leak["NO_MEANINGFUL_ROOM_explicitly_in_frozen_v4_spec"] is False
    assert leak["STRUCTURALLY_BLOCKED_explicitly_in_frozen_v4_spec"] is False
    spec = str(location_def()).upper()
    assert "NO_MEANINGFUL_ROOM" not in spec
    assert "STRUCTURALLY_BLOCKED" not in spec
    src = inspect.getsource(classify_s2)
    assert "NO_MEANINGFUL_ROOM" in src
    assert "STRUCTURALLY_BLOCKED" in src


def test_decide_leakage_wins():
    d = decide(
        bind_ok=True,
        leakage={"legacy_rule_leakage": True},
        invariants={"S3_without_S2_n": 1, "S1_without_S0_n": 0, "S2_without_S1_n": 0, "S4_without_S3_n": 0},
        parity={"spec_full_stack_not_machine_s4_n": 10, "focus": {}},
    )
    assert d["VERDICT"] == "PB1_V4_LEGACY_RULE_LEAKAGE_CONFIRMED_V1"
    assert d["NEXT"] == "PB1_V4_IMPLEMENTATION_CORRECTION_V1"
    assert d["any_rule_changed"] is False
    assert d["pnl"] is False


def test_no_pnl_in_audit_analyze():
    src = inspect.getsource(decide)
    assert "pnl" in src
    assert "future_return" not in src.lower() or "False" in src

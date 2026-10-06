"""Tests for V4 freeze and prospective prep. No PnL. Does not open prospective."""
from __future__ import annotations

from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    CASE_NBAR,
    CASE_READY,
    EXPECTED_V4_MACHINE_SHA256,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.analyze import decide
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import OUT, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.nbar import executable_stall_trace, static_scan


def test_v4_sha_frozen_and_overlap_zero():
    assert v4_sha() == EXPECTED_V4_MACHINE_SHA256
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep" in str(OUT).replace("\\", "/")


def test_nbar_constant_is_not_an_ast_predicate():
    scan = static_scan()
    assert scan["REPEATED_NO_EXPANSION_N_PRESENT"] is True
    assert scan["REPEATED_NO_EXPANSION_N_AST_PREDICATE"] is False
    assert scan["EXECUTABLE_DEATH_CONDITION_n"] == 0


def test_n_consecutive_pause_bars_do_not_kill_active():
    tr = executable_stall_trace()
    assert tr["n_bar_alone_killed"] is False
    assert tr["n_consecutive_bars_alone_cannot_kill_ACTIVE"] is True
    assert tr["wick_only_n_after_stall"] == 0
    assert tr["micro_break_n_after_stall"] == 0
    assert (tr["loss_after_12_stall_bars"] or {}).get("lost") is False


def test_decide_blocks_on_hidden_nbar():
    d = decide(
        bind_ok=True,
        nbar={"ok": False, "N_BAR_EXPIRY_USED": True, "HIDDEN_N_BAR_DEATH_PATH_FOUND": True},
        identity={"ok": True},
        ledger={"ok": True},
    )
    assert d["VERDICT"] == CASE_NBAR
    assert d["V4_FROZEN"] is False


def test_decide_ready_when_gates_pass():
    d = decide(
        bind_ok=True,
        nbar={"ok": True, "N_BAR_EXPIRY_USED": False, "HIDDEN_N_BAR_DEATH_PATH_FOUND": False},
        identity={"ok": True},
        ledger={"ok": True},
    )
    assert d["VERDICT"] == CASE_READY
    assert d["V4_FROZEN"] is True

"""ENTRY-aligned Full Causal logic. No Holdout/future. No Z3 candidate EXIT."""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from research.existing_data_entry_aligned_full_causal_logic_completion_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_PARITY,
    NEXT_A,
    NEXT_BC,
    NEXT_FIX,
    SELECTABLE_N,
    Z3_CANDIDATE_EXIT,
)
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.analyze import decide, integrity_gates
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.duplicates import audit_duplicates
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.publish import SHEET_ORDER
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.states import FALSE, TRUE, UNKNOWN, handoff_next_indices, persist_next_indices
from research.existing_data_entry_aligned_full_causal_logic_completion_v1.theses import build_theses, library_rows
from research.systematic_state_transition_library_precommit_v1.library import persist_id


def test_theses_pre_economics():
    rows = build_theses()
    assert len(rows) == 25
    assert sum(1 for r in rows if r["ENTRY_TYPE"] == "PERSIST_NEXT") == 5
    assert sum(1 for r in rows if r["ENTRY_TYPE"] == "HANDOFF_NEXT") == 20
    inel = [r for r in rows if not r["COMPLETE_STRATEGY_ELIGIBLE"]]
    assert len(inel) == 1
    assert inel[0]["ENTRY_ID"] == persist_id("S_VOL_CONFIRM_1M")
    assert inel[0]["INELIGIBLE_REASON"] == "NO_DURABLE_HOLDING_THESIS"
    sel = library_rows(rows)
    assert len(sel) == int(SELECTABLE_N)
    assert all(int(r["EXIT_VARIANTS_PER_ENTRY"]) == 1 for r in sel)
    assert all("Z3" not in str(r["TECHNICAL_EXIT_ID"]) for r in sel)
    rci_ma = next(r for r in sel if r["ENTRY_ID"] == "ST_HANDOFF_NEXT__S_MA_TREND_UP__S_RCI_ABOVE_NEG80")
    assert rci_ma["HOLDING_THESIS_STATES"] == ["S_MA_TREND_UP"]
    vol_ma = next(r for r in sel if r["ENTRY_ID"] == "ST_HANDOFF_NEXT__S_VOL_CONFIRM_1M__S_MA_TREND_UP")
    assert vol_ma["HOLDING_THESIS_STATES"] == ["S_MA_TREND_UP"]
    two = next(r for r in sel if r["ENTRY_ID"] == "ST_HANDOFF_NEXT__S_MA_TREND_UP__S_BB_ABOVE_MID")
    assert two["HOLDING_THESIS_STATES"] == ["S_MA_TREND_UP", "S_BB_ABOVE_MID"]
    rci_vol = next(r for r in sel if r["ENTRY_ID"] == "ST_HANDOFF_NEXT__S_RCI_ABOVE_NEG80__S_VOL_CONFIRM_1M")
    assert rci_vol["HOLDING_THESIS_STATES"] == ["S_RCI_ABOVE_NEG80"]
    dup = audit_duplicates(sel)
    assert dup["EXACT_PRIOR_DUPLICATE_N"] == 0
    integ = integrity_gates(theses=rows, dup=dup)
    assert integ["ALL_PASS"] is True
    assert integ["PASS_N"] == 29
    assert Z3_CANDIDATE_EXIT is False


def test_unknown_not_false_transitions():
    s = np.array([UNKNOWN, TRUE, TRUE], dtype=np.int8)
    assert persist_next_indices(s) == []
    s2 = np.array([FALSE, TRUE, TRUE], dtype=np.int8)
    assert persist_next_indices(s2) == [2]
    s3 = np.array([FALSE, TRUE, UNKNOWN], dtype=np.int8)
    assert persist_next_indices(s3) == []
    a = np.array([FALSE, TRUE, TRUE], dtype=np.int8)
    b = np.array([FALSE, FALSE, TRUE], dtype=np.int8)
    assert handoff_next_indices(a, b) == [2]
    b_unk = np.array([FALSE, FALSE, UNKNOWN], dtype=np.int8)
    assert handoff_next_indices(a, b_unk) == []
    assert UNKNOWN != FALSE


def test_decide_cases():
    p = decide(canary_ok=False, integrity_ok=True, robust_n=0, g15_n=0)
    assert p["VERDICT"] == CASE_PARITY
    assert p["NEXT"] == NEXT_FIX
    a = decide(canary_ok=True, integrity_ok=True, robust_n=1, g15_n=1)
    assert a["VERDICT"] == CASE_A
    assert a["NEXT"] == NEXT_A
    assert a["LOGIC_COMPLETE"] is True
    b = decide(canary_ok=True, integrity_ok=True, robust_n=0, g15_n=2)
    assert b["VERDICT"] == CASE_B
    assert b["NEXT"] == NEXT_BC
    assert b["LOGIC_COMPLETE"] is False
    c = decide(canary_ok=True, integrity_ok=True, robust_n=0, g15_n=0)
    assert c["VERDICT"] == CASE_C
    assert c["NEXT"] == NEXT_BC


def test_no_pnl_in_theses_and_no_z3_candidate():
    import research.existing_data_entry_aligned_full_causal_logic_completion_v1.theses as t

    src = inspect.getsource(t)
    assert "harvest_development" not in src
    assert "compute_pnl_yen_100" not in src
    assert "Z3_TWO_BAR" not in src


def test_sheet_order():
    assert len(SHEET_ORDER) == 22
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[5] == "entry_theses"
    assert SHEET_ORDER[-1] == "safety"
    _ = pytest

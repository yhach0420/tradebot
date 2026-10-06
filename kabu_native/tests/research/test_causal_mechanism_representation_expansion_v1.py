"""CAUSAL_MECHANISM_REPRESENTATION_EXPANSION_V1. Unit gate before new-mechanism outcomes."""
from __future__ import annotations

import numpy as np

from research.causal_mechanism_representation_expansion_v1 import (
    CASE_PARITY,
    CASE_UNIT,
    HORIZON_SEARCH,
    NEXT_PARITY,
    NEXT_UNIT,
    THRESHOLD_SEARCH,
)
from research.causal_mechanism_representation_expansion_v1.analyze import unit_gate
from research.causal_mechanism_representation_expansion_v1.library import (
    candidate_library,
    handoff_id,
    persist_id,
    reclaim_id,
)
from research.causal_mechanism_representation_expansion_v1.operators import (
    evaluate_row,
    handoff_next_at,
    persist_next_at,
    reclaim_accept_indices,
)
from research.causal_mechanism_representation_expansion_v1.publish import SHEET_ORDER
from research.causal_mechanism_representation_expansion_v1.reconstruct import identity_key, parity_stats
from research.causal_mechanism_representation_expansion_v1.spec import pin_parents
from research.profitable_move_mechanism_discovery_v1.harvest import assert_dev_day
from research.profitable_move_mechanism_discovery_v1.library import CROSS_PAIRS, RAW_PREDICATE_IDS
import pytest


def test_parent_pin_and_flags():
    pin = pin_parents()
    assert pin["ok"] is True
    assert pin["CANDIDATE_MECHANISM_N"] == 72
    assert pin["PASS_D1_D11_N"] == 0
    assert pin["FIXED_H5_HARD_GATE_VALID"] is True
    assert float(pin["P1_EXECUTED_H5_MEAN"]) > 0
    assert float(pin["P2_EXECUTED_H5_MEAN"]) > 0
    assert THRESHOLD_SEARCH is False
    assert HORIZON_SEARCH is False
    assert CASE_PARITY == "MECHANISM_REPRESENTATION_CONTROL_PARITY_FAILED_V1"
    assert CASE_UNIT == "DISCOVERY_UNIT_MISMATCH_PORTFOLIO_ADMISSION_MATTERS_V1"
    assert NEXT_PARITY == "FIX_REPRESENTATION_ONLY"
    assert NEXT_UNIT == "PORTFOLIO_ADMISSION_AWARE_MECHANISM_DISCOVERY_DESIGN_V1"


def test_library_frozen_finite_and_contains_controls():
    lib = candidate_library()
    ids = [r["MECHANISM_ID"] for r in lib]
    assert len(ids) == len(set(ids))
    assert len(lib) == len(RAW_PREDICATE_IDS) + len(CROSS_PAIRS) + 3
    assert persist_id("S_CLOSE_ABOVE_VWAP") in ids
    assert handoff_id("S_CLOSE_ABOVE_VWAP", "S_RCI_ABOVE_NEG80") in ids
    assert reclaim_id("R2") in ids
    assert reclaim_id("R1") in ids
    assert reclaim_id("R3") in ids
    templates = {r["TEMPLATE"] for r in lib}
    assert templates == {"O1_PERSIST_NEXT", "O2_HANDOFF_NEXT", "O3_RECLAIM_ACCEPT_NEXT"}
    p2 = next(r for r in lib if r["MECHANISM_ID"] == "O2_HANDOFF_NEXT__S_CLOSE_ABOVE_VWAP__S_RCI_ABOVE_NEG80")
    assert p2["PRIMITIVES"] == ["S_CLOSE_ABOVE_VWAP", "S_RCI_ABOVE_NEG80"]


def test_persist_and_handoff_operators():
    now = {"A": True, "B": True}
    prev = {"A": True, "B": False}
    prev2 = {"A": False, "B": False}
    assert persist_next_at(now, prev, prev2, "A") is True
    assert persist_next_at(now, {"A": False}, prev2, "A") is False
    assert handoff_next_at(now, prev, prev2, "A", "B") is True
    assert handoff_next_at(now, {"A": True, "B": True}, prev2, "A", "B") is False
    cand = {"TEMPLATE": "O1_PERSIST_NEXT", "PRIMITIVES": ["A"]}
    assert evaluate_row(cand, now, prev, prev2) is True
    cand2 = {"TEMPLATE": "O2_HANDOFF_NEXT", "PRIMITIVES": ["A", "B"]}
    assert evaluate_row(cand2, now, prev, prev2) is True


def test_reclaim_r2_needs_next_bar_accept():
    n = 6
    open_ = np.array([10, 10, 10, 11, 12, 12], dtype=float)
    high = np.array([10, 10, 10, 11, 12, 12], dtype=float)
    low = np.array([9, 9, 9, 10.6, 11.6, 11], dtype=float)
    close = np.array([9.5, 9.5, 10.5, 11.2, 12.0, 11.5], dtype=float)
    volume = np.array([1, 1, 1, 1, 1, 1], dtype=float)
    vwap = np.array([10, 10, 10, 10.2, 10.5, 10.7], dtype=float)
    # r=2: close 9.5<10 then 10.5>10 reclaim. c=3: low 10.6>10.2 and close 11.2>open 11
    xs = reclaim_accept_indices("R2", open_, high, low, close, volume, vwap)
    assert 3 in xs


def test_parity_requires_exact_identity():
    gold = [{"date": "20260807", "symbol": "A", "i": 4, "signal_t0": 10.1234564}]
    recon = [{"date": "20260807", "symbol": "A", "i": 4, "signal_t0": 10.1234561}]
    st = parity_stats(gold, recon)
    assert st["PARITY_PASS"] is True
    assert identity_key(gold[0]) == identity_key(recon[0])
    bad = [{"date": "20260807", "symbol": "A", "i": 5, "signal_t0": 10.1234567}]
    st2 = parity_stats(gold, bad)
    assert st2["PARITY_PASS"] is False
    assert st2["SIGNAL_IDENTITY_PRECISION"] == 0.0
    assert st2["SIGNAL_IDENTITY_RECALL"] == 0.0


def test_unit_gate_mismatch_and_valid():
    pin = {"P1_EXECUTED_H5_MEAN": 100.0, "P2_EXECUTED_H5_MEAN": 200.0}
    p1 = {"RAW_SIGNAL_H5_MEAN": 10.0, "RAW_SIGNAL_H5_RECOGNIZED": True}
    p2 = {"RAW_SIGNAL_H5_MEAN": 20.0, "RAW_SIGNAL_H5_RECOGNIZED": True}
    g = unit_gate(p1, p2, pin)
    assert g["DISCOVERY_UNIT_VALID"] is True
    assert g["PORTFOLIO_ADMISSION_MATTERS"] is False
    p2b = {"RAW_SIGNAL_H5_MEAN": -1.0, "RAW_SIGNAL_H5_RECOGNIZED": False}
    g2 = unit_gate(p1, p2b, pin)
    assert g2["RAW_SIGNAL_H5_DISCOVERY_VALID"] is False
    assert g2["PORTFOLIO_ADMISSION_MATTERS"] is True
    assert g2["P1_RAW_SIGNAL_H5_RECOGNIZED"] is True
    assert g2["P2_RAW_SIGNAL_H5_RECOGNIZED"] is False


def test_holdout_blocked_and_sheet_order():
    with pytest.raises(RuntimeError):
        assert_dev_day("20260810")
    with pytest.raises(RuntimeError):
        assert_dev_day("20260828")
    with pytest.raises(RuntimeError):
        assert_dev_day("20260903")
    assert SHEET_ORDER[0] == "answers"
    assert "gate" in SHEET_ORDER
    assert "safety" in SHEET_ORDER

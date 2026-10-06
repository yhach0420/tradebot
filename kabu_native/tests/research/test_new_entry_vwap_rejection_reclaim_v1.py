"""VWAP Rejection/Reclaim V1 tests. Duplicate false. First-cross of full reclaim state. No holdout-before-freeze."""
from __future__ import annotations

import numpy as np
import pytest

from research.new_entry_vwap_rejection_reclaim_v1 import (
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    PARAMETER_SEARCH_PERFORMED,
    STRESS_DAYS,
    TRUE_OOS,
)
from research.new_entry_vwap_rejection_reclaim_v1.already_executed import already_executed_check
from research.new_entry_vwap_rejection_reclaim_v1.analyze import decide, dev_gates, holdout_gates, pack_split
from research.new_entry_vwap_rejection_reclaim_v1.fallback import fallback_signal_at
from research.new_entry_vwap_rejection_reclaim_v1.harvest import AUDIT, assert_split_allowed
from research.new_entry_vwap_rejection_reclaim_v1.rule import p1_vwap_undercut, p2_vwap_reclaim, p3_bullish_bar, session_vwap, signal_at


def test_splits_and_bounds():
    assert len(DEVELOPMENT_DAYS) == 10
    assert len(LOCKED_HOLDOUT_DAYS) == 8
    assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")
    assert set(DEVELOPMENT_DAYS).isdisjoint(LOCKED_HOLDOUT_DAYS)
    assert set(DEVELOPMENT_DAYS).isdisjoint(STRESS_DAYS)
    assert MAX_RESEARCH_DATE == "20260902"
    assert FORBIDDEN_INPUT_DAYS == ("20260903", "20260904")
    assert "20260903" not in DEVELOPMENT_DAYS + LOCKED_HOLDOUT_DAYS + STRESS_DAYS
    assert TRUE_OOS is False
    assert CERTIFIED is False
    assert PARAMETER_SEARCH_PERFORMED is False


def test_not_duplicate_and_fallback_sealed():
    ae = already_executed_check()
    assert ae["DUPLICATE_ARCHITECTURE"] is False
    assert ae["PRIMARY_REQUIRED_VWAP_DATA_STRUCTURALLY_UNAVAILABLE"] is False
    assert ae["FALLBACK_ALLOWED"] is False
    assert ae["FALLBACK_USED"] is False
    names = {c["name"] for c in ae["comparisons"]}
    assert "E1_X18 VWAP late-chase reject" in names
    assert "X6 / X6R3 pullback reclaim" in names
    assert "EC2 pullback reclaim" in names
    assert "Simple-Tech VWAP-related rules" in names
    assert "Branch P VWAP EXIT" in names
    assert "BREAKOUT_CONTINUATION_V1" in names
    assert all(c["exact_same_architecture"] is False for c in ae["comparisons"])


def test_same_bar_undercut_reclaim_bullish():
    open_ = np.asarray([100.0, 100.0], dtype=float)
    low = np.asarray([99.0, 99.0], dtype=float)
    close = np.asarray([101.0, 101.0], dtype=float)
    vwap = np.asarray([100.5, 100.5], dtype=float)
    assert p1_vwap_undercut(low, vwap, 1) is True
    assert p2_vwap_reclaim(close, vwap, 1) is True
    assert p3_bullish_bar(open_, close, 1) is True
    low2 = np.asarray([99.0, 100.6], dtype=float)
    assert p1_vwap_undercut(low2, vwap, 1) is False
    close2 = np.asarray([101.0, 100.4], dtype=float)
    assert p2_vwap_reclaim(close2, vwap, 1) is False
    close3 = np.asarray([101.0, 99.5], dtype=float)
    assert p3_bullish_bar(open_, close3, 1) is False


def test_first_cross_full_state_not_repeat():
    n = 4
    open_ = np.asarray([100.0] * n, dtype=float)
    low = np.asarray([100.0, 99.0, 99.0, 100.0], dtype=float)
    close = np.asarray([100.0, 101.0, 101.5, 101.0], dtype=float)
    vwap = np.asarray([100.5] * n, dtype=float)
    s0 = signal_at(open_, low, close, vwap, 0)
    s1 = signal_at(open_, low, close, vwap, 1)
    s2 = signal_at(open_, low, close, vwap, 2)
    assert s0["SIGNAL"] is False
    assert s1["SIGNAL"] is True
    assert s1["FIRST_CROSS"] is True
    assert s2["RECLAIM_STATE"] is True
    assert s2["FIRST_CROSS"] is False
    assert s2["SIGNAL"] is False


def test_no_volume_or_prior_high_in_primary():
    text = signal_at.__doc__ or ""
    from research.new_entry_vwap_rejection_reclaim_v1.rule import EXACT_RULE_TEXT

    assert "max(High" not in EXACT_RULE_TEXT
    assert "median" not in EXACT_RULE_TEXT
    assert "RCI" not in EXACT_RULE_TEXT


def test_fallback_rule_exists_but_is_different():
    low = np.asarray([10, 9, 9, 9, 9, 8, 12], dtype=float)
    high = np.asarray([11, 11, 11, 11, 11, 9, 13], dtype=float)
    open_ = np.asarray([10, 10, 10, 10, 10, 9, 9], dtype=float)
    close = np.asarray([10, 10, 10, 10, 10, 8.5, 12], dtype=float)
    b = fallback_signal_at(open_, high, low, close, 6)
    assert b["SIGNAL"] is True
    vwap = session_vwap(close, np.ones(7) * 100.0)
    p = signal_at(open_, low, close, vwap, 6)
    assert p["SIGNAL"] is False


def test_holdout_blocked_before_freeze():
    AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] = 0
    with pytest.raises(RuntimeError, match="HOLDOUT_READ_BEFORE_RULE_FREEZE"):
        assert_split_allowed("HOLDOUT")
    assert AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] >= 1
    AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] = 0
    AUDIT["STRESS_READ_BEFORE_HOLDOUT_DECISION_N"] = 0
    with pytest.raises(RuntimeError):
        assert_split_allowed("STRESS")
    AUDIT["HOLDOUT_READ_BEFORE_RULE_FREEZE_N"] = 0
    AUDIT["STRESS_READ_BEFORE_HOLDOUT_DECISION_N"] = 0


def test_case_b_insufficient_coverage():
    p = pack_split([], list(DEVELOPMENT_DAYS))
    g = dev_gates(p)
    assert g["COVERAGE_PASS"] is False
    d = decide(
        integrity=True,
        dev_gate=g,
        hold_gate=None,
        stress_gate=None,
        exec_ok=None,
        holdout_opened=False,
        stress_opened=False,
        exec_ran=False,
    )
    assert d["CASE"] == "B"
    assert d["VERDICT"] == "NEW_ENTRY_VWAP_RECLAIM_COVERAGE_INSUFFICIENT"
    assert d["ENTRY_BASE_CANDIDATE_FROZEN"] is False
    assert d["EXIT_DESIGN_ALLOWED"] is False


def test_holdout_gate_pos_ge_neg():
    g = holdout_gates(
        {
            "EXECUTION_EVALUABLE_N": 32,
            "MEAN_180": 1.0,
            "MEAN_300": 1.0,
            "positive_day_n_180": 4,
            "negative_day_n_180": 4,
            "positive_day_n_300": 4,
            "negative_day_n_300": 4,
            "EX_BEST_DAY_MEAN_180": 0.0,
            "EX_BEST_DAY_MEAN_300": 0.1,
            "DROP_TOP_SYMBOL_MEAN_180": 0.0,
            "DROP_TOP_SYMBOL_MEAN_300": 0.0,
        }
    )
    assert g["PASS"] is True


def test_case_e_on_integrity():
    d = decide(
        integrity=False,
        dev_gate={"PASS": True, "COVERAGE_PASS": True},
        hold_gate=None,
        stress_gate=None,
        exec_ok=None,
        holdout_opened=False,
        stress_opened=False,
        exec_ran=False,
    )
    assert d["CASE"] == "E"
    assert d["VERDICT"] == "NEW_ENTRY_VWAP_RECLAIM_INTEGRITY_FAILED"

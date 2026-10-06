"""Breakout Continuation V1 tests. No holdout-before-freeze. No 20260903/04."""
from __future__ import annotations

import numpy as np
import pytest

from research.new_entry_breakout_continuation_v1 import (
    CERTIFIED,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    HIGH_WINDOW,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    PARAMETER_SEARCH_PERFORMED,
    STRESS_DAYS,
    TRUE_OOS,
    VOLUME_WINDOW,
)
from research.new_entry_breakout_continuation_v1.analyze import decide, dev_gates, holdout_gates, pack_split
from research.new_entry_breakout_continuation_v1.harvest import AUDIT, assert_split_allowed
from research.new_entry_breakout_continuation_v1.rule import p1_price_breakout, p2_volume_expansion, signal_at, session_vwap


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
    assert HIGH_WINDOW == 5
    assert VOLUME_WINDOW == 10


def test_p1_uses_close_not_current_high():
    high = np.asarray([1, 1, 1, 1, 1, 10.0], dtype=float)
    close = np.asarray([1, 1, 1, 1, 1, 1.5], dtype=float)
    assert p1_price_breakout(high, close, 5) is True
    close2 = np.asarray([1, 1, 1, 1, 1, 1.0], dtype=float)
    assert p1_price_breakout(high, close2, 5) is False


def test_volume_median_excludes_current_and_has_no_multiplier():
    vol = np.asarray([10.0] * 10 + [10.1], dtype=float)
    assert p2_volume_expansion(vol, 10) is True
    vol2 = np.asarray([10.0] * 10 + [10.0], dtype=float)
    assert p2_volume_expansion(vol2, 10) is False


def test_first_cross_not_repeat():
    n = 16
    high = np.ones(n)
    close = np.ones(n)
    vol = np.ones(n) * 5.0
    vol[10:] = 20.0
    close[10] = 2.0
    high[10] = 1.8
    close[11] = 2.2
    high[11] = 2.0
    vwap = session_vwap(close, vol)
    s10 = signal_at(high, close, vol, vwap, 10)
    s11 = signal_at(high, close, vol, vwap, 11)
    assert s10["P1"] is True
    assert s10["FIRST_CROSS"] is True
    assert s10["SIGNAL"] is True
    assert s11["P1"] is True
    assert s11["FIRST_CROSS"] is False
    assert s11["SIGNAL"] is False


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
    assert d["EXIT_DESIGN_ALLOWED"] is False

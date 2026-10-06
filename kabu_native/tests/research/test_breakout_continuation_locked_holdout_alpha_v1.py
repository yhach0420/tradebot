"""Locked holdout MID alpha tests. Freeze before holdout. Stress sealed. No VWAP hop."""
from __future__ import annotations

import numpy as np
import pytest

from research.breakout_continuation_locked_holdout_alpha_v1 import (
    HIGH_WINDOW,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    MIN_HOLDOUT_TOTAL_N,
    PARAMETER_CHANGE_N,
    VOLUME_WINDOW,
    VWAP_HOLDOUT_ALLOWED,
)
from research.breakout_continuation_locked_holdout_alpha_v1.analyze import (
    CASE_A,
    CASE_C,
    CASE_D,
    alpha_gates,
    coverage_gates,
    decide,
    trimmed_mean_5p,
    winsorized_mean_5p,
)
from research.breakout_continuation_locked_holdout_alpha_v1.harvest import AUDIT, assert_holdout_day
from research.new_entry_breakout_continuation_v1.rule import EXACT_RULE_TEXT


def test_bounds_and_freeze_constants():
    assert MAX_RESEARCH_DATE == "20260827"
    assert len(LOCKED_HOLDOUT_DAYS) == 8
    assert "20260828" not in LOCKED_HOLDOUT_DAYS
    assert VWAP_HOLDOUT_ALLOWED is False
    assert PARAMETER_CHANGE_N == 0
    assert HIGH_WINDOW == 5
    assert VOLUME_WINDOW == 10
    assert MIN_HOLDOUT_TOTAL_N == 480
    assert "max(High[i-5]" in EXACT_RULE_TEXT


def test_holdout_blocked_before_freeze_and_stress_blocked():
    AUDIT["HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    with pytest.raises(RuntimeError, match="HOLDOUT_READ_BEFORE_PRIMARY_FREEZE"):
        assert_holdout_day("20260810")
    assert AUDIT["HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N"] >= 1
    AUDIT["HOLDOUT_READ_BEFORE_PRIMARY_FREEZE_N"] = 0
    with pytest.raises(RuntimeError, match="STRESS_READ"):
        assert_holdout_day("20260828")
    assert AUDIT["STRESS_READ_N"] >= 1
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["FUTURE_DATA_N"] = 0
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_holdout_day("20260903")
    AUDIT["FUTURE_DATA_N"] = 0


def test_trimmed_and_winsor():
    xs = list(range(100))
    t = trimmed_mean_5p(xs)
    w = winsorized_mean_5p(xs)
    assert t is not None and w is not None
    assert abs(float(t) - float(np.mean(xs[5:95]))) < 1e-9


def test_case_d_coverage():
    d = decide(
        integrity=True,
        coverage={"PASS": False},
        alpha={"PASS": True, "TAIL_FRAGILE": False, "gates": {}},
        hold={"MEAN_MID_180": 1.0, "MEAN_MID_300": 1.0},
    )
    assert d["CASE"] == "D"
    assert d["VERDICT"] == CASE_D


def test_case_c_mid_nonpositive():
    d = decide(
        integrity=True,
        coverage={"PASS": True},
        alpha={"PASS": False, "TAIL_FRAGILE": False, "LIFT180": 1.0, "LIFT300": 1.0, "gates": {}},
        hold={"MEAN_MID_180": -0.1, "MEAN_MID_300": 1.0},
    )
    assert d["CASE"] == "C"
    assert d["VERDICT"] == CASE_C
    assert d["BREAKOUT_PERMANENTLY_CLOSED"] is True
    assert d["VWAP_HOLDOUT_TEST_ALLOWED"] is False


def test_case_a_all_gates():
    hold = {
        "MEAN_MID_180": 1.0,
        "MEAN_MID_300": 1.0,
        "MID_DAY_SIGNS_180": {"positive_day_n": 5, "negative_day_n": 3},
        "MID_DAY_SIGNS_300": {"positive_day_n": 4, "negative_day_n": 4},
        "EX_BEST_MID_180": 0.0,
        "EX_BEST_MID_300": 0.1,
        "DROP_TOP_MID_180": 0.0,
        "DROP_TOP_MID_300": 0.2,
        "TRIMMED_MEAN_5P_180": 0.4,
        "TRIMMED_MEAN_5P_300": 0.3,
        "WINSORIZED_MEAN_5P_180": 0.5,
        "WINSORIZED_MEAN_5P_300": 0.2,
    }
    uncond = {"MEAN_MID_180": 0.1, "MEAN_MID_300": 0.1}
    ag = alpha_gates(hold, uncond)
    assert ag["PASS"] is True
    d = decide(integrity=True, coverage={"PASS": True}, alpha=ag, hold=hold)
    assert d["CASE"] == "A"
    assert d["VERDICT"] == CASE_A
    assert d["PROFIT_CANDIDATE"] is False
    assert d["EXECUTION_CANDIDATE"] is False


def test_coverage_min_480():
    daily = [{"date": d, "DAILY_EVALUABLE_N": 60} for d in LOCKED_HOLDOUT_DAYS]
    g = coverage_gates({"EVALUABLE_N": 480, "daily": daily}, {d: True for d in LOCKED_HOLDOUT_DAYS})
    assert g["PASS"] is True
    g2 = coverage_gates({"EVALUABLE_N": 479, "daily": daily}, {d: True for d in LOCKED_HOLDOUT_DAYS})
    assert g2["PASS"] is False

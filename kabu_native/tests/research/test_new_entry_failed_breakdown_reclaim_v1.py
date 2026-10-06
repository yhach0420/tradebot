"""FDR DEV-only MID alpha tests. Stress sealed. Burned holdout sealed. No first-cross."""
from __future__ import annotations

import numpy as np
import pytest

from research.new_entry_failed_breakdown_reclaim_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FIRST_CROSS_ADDED,
    MAX_RESEARCH_DATE,
    MIN_DEV_TOTAL_N,
    PARAMETER_CHANGE_N,
    STRESS_DAYS,
)
from research.new_entry_failed_breakdown_reclaim_v1.analyze import (
    CASE_A,
    CASE_C,
    CASE_D,
    alpha_gates,
    cluster_diagnostics,
    coverage_gates,
    decide,
)
from research.new_entry_failed_breakdown_reclaim_v1.harvest import AUDIT, assert_dev_only_day
from research.new_entry_failed_breakdown_reclaim_v1.rule import EXACT_RULE_TEXT, signal_at
from research.new_entry_failed_breakdown_reclaim_v1.spec import duplicate_architecture


def test_bounds_and_rule_constants():
    assert MAX_RESEARCH_DATE == "20260807"
    assert len(DEVELOPMENT_DAYS) == 10
    assert len(BURNED_HOLDOUT_DAYS) == 8
    assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")
    assert "20260810" not in DEVELOPMENT_DAYS
    assert "20260828" not in DEVELOPMENT_DAYS
    assert FIRST_CROSS_ADDED is False
    assert PARAMETER_CHANGE_N == 0
    assert MIN_DEV_TOTAL_N == 600
    assert "Low[i-1] < min(Low[i-6]" in EXACT_RULE_TEXT
    assert "No first-cross" in EXACT_RULE_TEXT


def test_not_duplicate():
    d = duplicate_architecture()
    assert d["DUPLICATE_VS_BREAKOUT"] is False
    assert d["DUPLICATE_VS_VWAP_RECLAIM"] is False
    assert d["DUPLICATE_ARCHITECTURE"] is False


def test_exact_signal_no_first_cross():
    n = 8
    open_ = np.asarray([10.0] * n, dtype=float)
    high = np.asarray([11.0] * n, dtype=float)
    low = np.asarray([9.0, 9.0, 9.0, 9.0, 9.0, 8.0, 8.5, 8.4], dtype=float)
    close = np.asarray([10.0, 10.0, 10.0, 10.0, 10.0, 9.0, 11.5, 11.6], dtype=float)
    high[5] = 9.5
    s5 = signal_at(open_, high, low, close, 5)
    s6 = signal_at(open_, high, low, close, 6)
    assert s5["SIGNAL"] is False
    assert s6["BREAKDOWN_PREV"] is True
    assert s6["RECLAIM_NOW"] is True
    assert s6["SIGNAL"] is True
    close_dn = close.copy()
    close_dn[6] = 9.4
    assert signal_at(open_, high, low, close_dn, 6)["SIGNAL"] is False


def test_dev_only_blocks_holdout_and_stress():
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["STRESS_FILE_OPEN_N"] = 0
    AUDIT["STRESS_METRIC_COMPUTE_N"] = 0
    AUDIT["FUTURE_DATA_N"] = 0
    with pytest.raises(RuntimeError, match="HOLDOUT_BURNED_READ"):
        assert_dev_only_day("20260810")
    assert AUDIT["HOLDOUT_BURNED_READ_N"] >= 1
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    with pytest.raises(RuntimeError, match="STRESS_READ"):
        assert_dev_only_day("20260828")
    assert AUDIT["STRESS_READ_N"] >= 1
    assert AUDIT["STRESS_FILE_OPEN_N"] >= 1
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["STRESS_FILE_OPEN_N"] = 0
    AUDIT["STRESS_METRIC_COMPUTE_N"] = 0
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260903")
    AUDIT["FUTURE_DATA_N"] = 0


def test_cluster_equal_weight_not_count_weighted():
    rows = [
        {"date": "d1", "symbol": "A", "eligible": True, "MID_180": 10.0},
        {"date": "d1", "symbol": "A", "eligible": True, "MID_180": 10.0},
        {"date": "d1", "symbol": "A", "eligible": True, "MID_180": 10.0},
        {"date": "d1", "symbol": "B", "eligible": True, "MID_180": -2.0},
    ]
    cl = cluster_diagnostics(rows, rows)
    assert cl["UNIQUE_SYMBOL_N"] == 2
    assert cl["UNIQUE_SYMBOL_DAY_N"] == 2
    assert cl["HARD_GATE"] is False
    assert abs(float(cl["CLUSTER_EQUAL_WEIGHT_MID180"]) - 4.0) < 1e-9


def test_case_d_and_c_and_a():
    d = decide(integrity=True, coverage={"PASS": False}, alpha={"PASS": True, "TAIL_FRAGILE": False}, dev=None)
    assert d["CASE"] == "D"
    assert d["VERDICT"] == CASE_D
    assert d["FAMILY_CLOSED"] is True
    assert d["NEXT"] == "SIMPLE_ENTRY_STATE_DISCOVERY_V1"
    d2 = decide(
        integrity=True,
        coverage={"PASS": True},
        alpha={"PASS": False, "TAIL_FRAGILE": False, "LIFT180": 1.0, "LIFT300": 1.0, "gates": {}},
        dev={"MEAN_MID_180": -0.1, "MEAN_MID_300": 1.0},
    )
    assert d2["CASE"] == "C"
    assert d2["VERDICT"] == CASE_C
    hold = {
        "MEAN_MID_180": 1.0,
        "MEAN_MID_300": 1.0,
        "MID_DAY_SIGNS_180": {"positive_day_n": 6, "negative_day_n": 4},
        "MID_DAY_SIGNS_300": {"positive_day_n": 5, "negative_day_n": 5},
        "EX_BEST_MID_180": 0.0,
        "EX_BEST_MID_300": 0.1,
        "DROP_TOP_MID_180": 0.0,
        "DROP_TOP_MID_300": 0.2,
        "TRIMMED_MEAN_5P_180": 0.4,
        "TRIMMED_MEAN_5P_300": 0.3,
        "WINSORIZED_MEAN_5P_180": 0.5,
        "WINSORIZED_MEAN_5P_300": 0.2,
    }
    ag = alpha_gates(hold, {"MEAN_MID_180": 0.1, "MEAN_MID_300": 0.1})
    assert ag["PASS"] is True
    d3 = decide(integrity=True, coverage={"PASS": True}, alpha=ag, dev=hold)
    assert d3["CASE"] == "A"
    assert d3["VERDICT"] == CASE_A
    assert d3["ENTRY_BASE_CANDIDATE_FROZEN"] is False
    assert d3["STRESS_OPENED"] is False
    assert "EXECUTION_DESIGN_V1" in d3["NEXT"]


def test_coverage_min_600():
    daily = [{"date": d, "DAILY_EVALUABLE_N": 60} for d in DEVELOPMENT_DAYS]
    g = coverage_gates({"EVALUABLE_N": 600, "daily": daily}, {d: True for d in DEVELOPMENT_DAYS})
    assert g["PASS"] is True
    g2 = coverage_gates({"EVALUABLE_N": 599, "daily": daily}, {d: True for d in DEVELOPMENT_DAYS})
    assert g2["PASS"] is False

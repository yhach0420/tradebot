"""DISCOVERY_SEARCH_SPACE_REASSESSMENT_V1. Calibration only. No new strategy."""
from __future__ import annotations

import numpy as np

from research.discovery_search_space_reassessment_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    HORIZON_SEARCH,
    NEW_STRATEGY_CREATED,
    NEXT_B,
    P1_EXPECTED_PNL,
    P1_EXPECTED_TRADE_N,
    P2_EXPECTED_PNL,
    P2_EXPECTED_TRADE_N,
    THRESHOLD_SEARCH,
)
from research.discovery_search_space_reassessment_v1.analyze import hold_buckets, sign_matrix
from research.discovery_search_space_reassessment_v1.controls import recover_all
from research.discovery_search_space_reassessment_v1.engine import first_in, last_le_mid
from research.discovery_search_space_reassessment_v1.publish import SHEET_ORDER
from research.discovery_search_space_reassessment_v1.spec import pin_parent


def test_parent_pin():
    pin = pin_parent()
    assert pin["ok"] is True
    assert pin["CANDIDATE_MECHANISM_N"] == 72
    assert pin["COVERAGE_QUALIFIED_N"] == 68
    assert pin["PASS_D1_D11_N"] == 0
    assert pin["ALL_COVERAGE_QUALIFIED_H5_ABS_MEAN_NEGATIVE"] is True
    assert pin["FEATURE_LOOKAHEAD_N"] == 0
    assert pin["FULL_STRATEGY_FROZEN"] is False
    assert THRESHOLD_SEARCH is False
    assert HORIZON_SEARCH is False
    assert NEW_STRATEGY_CREATED is False
    assert CASE_A != CASE_B != CASE_C
    assert NEXT_B == "PATH_AWARE_PROFITABLE_MOVE_DISCOVERY_DESIGN_V1"


def test_exact_executed_cohorts_match_existing_full_causal():
    got = recover_all()
    assert got["P1"]["CONTROL_EXECUTED_COHORT_AVAILABLE"] is True
    assert got["P1"]["TRADE_N"] == P1_EXPECTED_TRADE_N
    assert abs(float(got["P1"]["ACTUAL_FULL_STRATEGY_TOTAL_PNL"]) - float(P1_EXPECTED_PNL)) < 1e-6
    assert got["P1"]["X1_EXEC_ID_ALL"] is True
    assert got["P2"]["CONTROL_EXECUTED_COHORT_AVAILABLE"] is True
    assert got["P2"]["TRADE_N"] == P2_EXPECTED_TRADE_N
    assert abs(float(got["P2"]["ACTUAL_FULL_STRATEGY_TOTAL_PNL"]) - float(P2_EXPECTED_PNL)) < 1e-6
    assert got["NEGATIVE"]["CONTROL_EXECUTED_COHORT_AVAILABLE"] is True
    t = got["P1"]["trades"][0]
    for k in ("symbol", "signal_t0", "entry_fill_t", "entry_fill_price", "exit_fill_t", "actual_pnl_yen100"):
        assert t.get(k) is not None


def test_hold_and_sign_helpers():
    rows = [
        {"hold_sec": 60.0, "actual_pnl_yen100": 100.0, "exec_yen100_h5": -10.0},
        {"hold_sec": 200.0, "actual_pnl_yen100": 50.0, "exec_yen100_h5": 10.0},
        {"hold_sec": 400.0, "actual_pnl_yen100": -20.0, "exec_yen100_h5": -5.0},
        {"hold_sec": 800.0, "actual_pnl_yen100": 10.0, "exec_yen100_h5": 0.0},
    ]
    b = hold_buckets(rows)
    assert b["exit_before_3m_n"] == 1
    assert b["exit_3_to_5m_n"] == 1
    assert b["exit_5_to_10m_n"] == 1
    assert b["exit_after_10m_n"] == 1
    s = sign_matrix(rows)
    assert s["actual_profit_and_H5_positive_n"] == 1
    assert s["actual_profit_and_H5_nonpositive_n"] == 2
    assert s["actual_loss_and_H5_nonpositive_n"] == 1
    assert abs(float(s["fraction_profitable_H5_le0"]) - 2.0 / 3.0) < 1e-12


def test_quote_window_helpers():
    t = np.array([100.0, 160.0, 200.0])
    px = np.array([1.0, 2.0, 3.0])
    et, ep = first_in(t, px, 150.0, 180.0)
    assert et == 160.0 and ep == 2.0
    mt = np.array([90.0, 110.0])
    bid = np.array([10.0, 12.0])
    ask = np.array([11.0, 13.0])
    assert abs(float(last_le_mid(mt, bid, ask, 100.0)) - 10.5) < 1e-12


def test_sheet_order():
    assert SHEET_ORDER[0] == "answers"
    assert "decision" in SHEET_ORDER
    assert "safety" in SHEET_ORDER

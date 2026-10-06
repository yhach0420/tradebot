"""Recovery Sequence Full Strategy tests. 6 candidates. No E4. No queue fill. Stress sealed."""
from __future__ import annotations

import numpy as np
import pytest

from research.recovery_sequence_full_strategy_architecture_v1 import (
    CANDIDATE_IDS,
    CANDIDATE_N,
    CASE_A,
    CASE_B,
    CASE_D,
    CASE_E,
    E4_X2_Z3_CLOSED,
    MAX_RESEARCH_DATE,
)
from research.recovery_sequence_full_strategy_architecture_v1.analyze import coverage_ok, decide, economic_base_ok
from research.recovery_sequence_full_strategy_architecture_v1.entries import already_executed_check, reclaim_arm, signal_indices
from research.recovery_sequence_full_strategy_architecture_v1.execution import evaluate_execution
from research.recovery_sequence_full_strategy_architecture_v1.harvest import AUDIT, assert_dev_only_day
from research.recovery_sequence_full_strategy_architecture_v1.spec import candidate_ids


def test_grid_is_exactly_6_no_e4():
    ids = candidate_ids()
    assert ids == list(CANDIDATE_IDS)
    assert len(ids) == 6 == int(CANDIDATE_N)
    assert "E4_X2_Z3" not in ids
    assert E4_X2_Z3_CLOSED is True
    assert MAX_RESEARCH_DATE == "20260807"
    assert already_executed_check()["duplicate"] is False


def test_r1_next_bar_only_then_abort():
    n = 6
    open_ = np.asarray([10.0] * n)
    high = np.asarray([11.0] * n)
    low = np.asarray([9.0] * n)
    close = np.asarray([9.0, 10.5, 10.2, 9.5, 10.6, 10.3])
    volume = np.asarray([100.0] * n)
    vwap = np.asarray([10.0] * n)
    assert reclaim_arm(close, vwap, 1) is True
    assert reclaim_arm(close, vwap, 4) is True
    sig = signal_indices("R1", open_, high, low, close, volume, vwap)
    assert 2 not in sig
    assert 5 not in sig
    assert sig == []


def test_r1_followthrough_fires():
    close = np.asarray([9.0, 10.5, 11.0])
    vwap = np.asarray([10.0, 10.0, 10.0])
    open_ = np.asarray([10.0, 10.0, 10.0])
    high = np.asarray([11.0, 11.0, 11.5])
    low = np.asarray([8.5, 10.0, 10.5])
    volume = np.asarray([1.0, 1.0, 1.0])
    sig = signal_indices("R1", open_, high, low, close, volume, vwap)
    assert sig == [2]


def test_r2_needs_low_above_vwap():
    close = np.asarray([9.0, 10.5, 11.0])
    vwap = np.asarray([10.0, 10.0, 10.0])
    open_ = np.asarray([10.0, 10.0, 10.5])
    high = np.asarray([11.0, 11.0, 11.5])
    low = np.asarray([8.5, 9.5, 10.2])
    volume = np.asarray([1.0, 1.0, 1.0])
    assert signal_indices("R2", open_, high, low, close, volume, vwap) == [2]
    low2 = np.asarray([8.5, 9.5, 9.8])
    assert signal_indices("R2", open_, high, low2, close, volume, vwap) == []


def test_x2_fills_at_ask_not_mid():
    n = 3
    t = np.asarray([100.0, 101.0, 102.0])
    board = {
        "t": t,
        "ask": np.asarray([11.0, 10.4, 10.4]),
        "bid": np.asarray([10.0, 10.0, 10.0]),
        "ask_qty": np.asarray([100.0, 100.0, 100.0]),
        "bid_qty": np.asarray([100.0, 100.0, 100.0]),
        "ask_fresh_sec": np.asarray([0.1, 0.1, 0.1]),
        "bid_fresh_sec": np.asarray([0.1, 0.1, 0.1]),
        "fresh_sec": np.asarray([0.1, 0.1, 0.1]),
        "executable": np.asarray([True, True, True]),
        "continuous": np.asarray([True, True, True]),
        "special": np.asarray([False, False, False]),
        "board_execution_state": np.asarray(["CONTINUOUS_TRADING"] * 3),
        "fresh_source": np.asarray(["BOARD_EVENT_TIME"] * 3),
    }
    got = evaluate_execution(board, exec_id="X2", t0=100.0, sess_end=200.0)
    assert got["WOULD_FILL"] is True
    assert abs(float(got["fill_price"]) - 10.4) < 1e-12
    mid = (11.0 + 10.0) / 2.0
    assert abs(float(got["fill_price"]) - mid) > 1e-9
    assert int(got["QUEUE_ASSUMED_FILL"]) == 0
    assert int(got["BAR_OHLC_FILL"]) == 0


def test_dev_only_blocks():
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["FUTURE_DATA_N"] = 0
    with pytest.raises(RuntimeError, match="HOLDOUT_BURNED_READ"):
        assert_dev_only_day("20260810")
    AUDIT["HOLDOUT_BURNED_READ_N"] = 0
    with pytest.raises(RuntimeError, match="STRESS_READ"):
        assert_dev_only_day("20260828")
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["STRESS_FILE_OPEN_N"] = 0
    AUDIT["STRESS_METRIC_COMPUTE_N"] = 0
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260903")
    AUDIT["FUTURE_DATA_N"] = 0


def test_gates_and_cases():
    assert coverage_ok({"TRADE_N": 39, "TRADING_DAY_WITH_FILL_N": 8, "trades_per_day": 4.0}) is False
    assert coverage_ok({"TRADE_N": 40, "TRADING_DAY_WITH_FILL_N": 8, "trades_per_day": 4.0}) is True
    eco = {"TOTAL_PNL": 100.0, "PF": 1.2, "positive_day_n": 6, "negative_day_n": 4, "EX_BEST_DAY_PNL": 10.0, "MAXDD": -20.0}
    assert economic_base_ok(eco) is True
    d = decide(integrity=False, exec_integ=True, coverage_pass_n=1, pass_n=1, winner_id="x", lodo_pack={"stable": True})
    assert d["VERDICT"] == CASE_E
    d = decide(integrity=True, exec_integ=True, coverage_pass_n=0, pass_n=0, winner_id=None, lodo_pack=None)
    assert d["VERDICT"] == CASE_D
    d = decide(integrity=True, exec_integ=True, coverage_pass_n=3, pass_n=0, winner_id=None, lodo_pack=None)
    assert d["VERDICT"] == CASE_B
    d = decide(integrity=True, exec_integ=True, coverage_pass_n=3, pass_n=1, winner_id="R1_X1_Z3", lodo_pack={"stable": True})
    assert d["VERDICT"] == CASE_A
    assert d["FULL_STRATEGY_DEV_FROZEN"] is True
    assert d["STRESS_OPENED"] is False

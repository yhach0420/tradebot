"""Participation Onset Full Strategy tests. Frozen T1. 3 candidates. No VWAP filter. Stress sealed."""
from __future__ import annotations

import numpy as np
import pytest

from research.dynamic_anchor_p2_0b.contract import t1_raw
from research.participation_onset_full_strategy_v1 import (
    ABOVE_VWAP_ENTRY_FILTER,
    CANDIDATE_IDS,
    CANDIDATE_N,
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    INDEPENDENT_ALPHA_CLAIM,
    MAX_RESEARCH_DATE,
    RECOVERY_CLOSED,
    RPFE_EPISODE_INDEPENDENCE_CLAIM,
    THRESHOLD_RETUNED,
    VOLUME_PERCENTILE_THRESHOLD,
)
from research.participation_onset_full_strategy_v1.analyze import coverage_ok, decide, economic_base_ok
from research.participation_onset_full_strategy_v1.entries import already_executed_check, onset_rows, previous_low
from research.participation_onset_full_strategy_v1.execution import evaluate_execution
from research.participation_onset_full_strategy_v1.exits import zp_trigger_t
from research.participation_onset_full_strategy_v1.harvest import AUDIT, assert_dev_only_day
from research.participation_onset_full_strategy_v1.spec import candidate_ids


def _row(*, epoch: float, volp, feature="OK", relative="OK", n=20, symbol="AAA"):
    return {
        "symbol": symbol,
        "session": "AM",
        "grid_epoch": epoch,
        "feature_status": feature,
        "relative_status": relative,
        "rs_universe_n": n,
        "volume_percentile_60s": volp,
    }


def test_grid_is_exactly_3_frozen_t1():
    ids = candidate_ids()
    assert ids == list(CANDIDATE_IDS)
    assert len(ids) == 3 == int(CANDIDATE_N)
    assert ids == ["P1_X1_Z3", "P1_X1_ZP", "P1_X1_ZH"]
    assert RECOVERY_CLOSED is True
    assert MAX_RESEARCH_DATE == "20260807"
    assert VOLUME_PERCENTILE_THRESHOLD == 0.6486486486486487
    assert ABOVE_VWAP_ENTRY_FILTER is False
    assert THRESHOLD_RETUNED is False
    assert INDEPENDENT_ALPHA_CLAIM is False
    assert RPFE_EPISODE_INDEPENDENCE_CLAIM is False
    assert already_executed_check()["duplicate"] is False
    assert already_executed_check()["DUPLICATE_FULL_STRATEGY"] is False


def test_t1_raw_missing_is_false_and_threshold_not_retuned():
    assert t1_raw(_row(epoch=1, volp=None)) is False
    assert t1_raw(_row(epoch=1, volp=float("nan"))) is False
    assert t1_raw(_row(epoch=1, volp=0.6486486486486487)) is True
    assert t1_raw(_row(epoch=1, volp=0.6486486486486486)) is False
    assert previous_low(_row(epoch=1, volp=0.5)) is True
    assert previous_low(_row(epoch=1, volp=None, feature="FEATURE_NOT_EVALUABLE")) is False


def test_onset_requires_previous_evaluable_low():
    rows = [
        _row(epoch=100.0, volp=0.50),
        _row(epoch=110.0, volp=0.70),
        _row(epoch=120.0, volp=0.80),
        _row(epoch=130.0, volp=0.40),
        _row(epoch=140.0, volp=0.70),
    ]
    got = onset_rows(rows)
    assert [r["grid_epoch"] for r in got] == [110.0, 140.0]
    first_only = onset_rows([_row(epoch=100.0, volp=0.90)])
    assert first_only == []
    missing_prev = onset_rows(
        [
            _row(epoch=100.0, volp=None, feature="FEATURE_NOT_EVALUABLE"),
            _row(epoch=110.0, volp=0.90),
        ]
    )
    assert missing_prev == []


def test_onset_ignores_vwap_fields():
    r0 = _row(epoch=100.0, volp=0.40)
    r1 = _row(epoch=110.0, volp=0.90)
    r1["distance_from_vwap_bps"] = -50.0
    got = onset_rows([r0, r1])
    assert len(got) == 1
    assert got[0]["distance_from_vwap_bps"] == -50.0


def test_zp_skips_missing_then_fires_on_low():
    grid = [
        {"grid_epoch": 200.0, "evaluable": False, "volume_percentile_60s": None},
        {"grid_epoch": 210.0, "evaluable": True, "volume_percentile_60s": 0.80},
        {"grid_epoch": 220.0, "evaluable": True, "volume_percentile_60s": 0.40},
    ]
    assert zp_trigger_t(grid, fill_t=190.0) == 220.0
    assert zp_trigger_t(grid, fill_t=220.0) is None


def test_x1_fills_at_ask_not_mid():
    t = np.asarray([100.0, 101.0, 102.0])
    board = {
        "t": t,
        "ask": np.asarray([11.0, 11.0, 11.0]),
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
    got = evaluate_execution(board, t0=100.0, sess_end=200.0)
    assert got["WOULD_FILL"] is True
    assert abs(float(got["fill_price"]) - 11.0) < 1e-12
    mid = (11.0 + 10.0) / 2.0
    assert abs(float(got["fill_price"]) - mid) > 1e-9
    assert int(got["QUEUE_ASSUMED_FILL"]) == 0
    assert int(got["BAR_OHLC_FILL"]) == 0
    assert int(got["TRADE_PRINT_PASSIVE_FILL"]) == 0


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
    d = decide(integrity=True, exec_integ=True, coverage_pass_n=3, pass_n=1, winner_id="P1_X1_Z3", lodo_pack={"stable": False})
    assert d["VERDICT"] == CASE_C
    d = decide(integrity=True, exec_integ=True, coverage_pass_n=3, pass_n=1, winner_id="P1_X1_Z3", lodo_pack={"stable": True})
    assert d["VERDICT"] == CASE_A
    assert d["FULL_STRATEGY_DEV_FROZEN"] is True
    assert d["STRESS_OPENED"] is False

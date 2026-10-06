"""Full-strategy discovery tests. 75 frozen candidates. Stress sealed. No extra ENTRY."""
from __future__ import annotations

import numpy as np
import pytest

from research.simple_full_strategy_discovery_v1 import (
    BURNED_HOLDOUT_DAYS,
    CANDIDATE_N,
    DEVELOPMENT_DAYS,
    ENTRY_IDS,
    EXEC_IDS,
    EXIT_IDS,
    MAX_RESEARCH_DATE,
    POSITION_CAP,
    SHARES,
    STRESS_DAYS,
    WAIT_SEC,
)
from research.simple_full_strategy_discovery_v1.analyze import CASE_A, CASE_B, CASE_C, coverage_ok, decide, economic_ok, rank_pass, score_of
from research.simple_tech_entry_family.portfolio import portfolio_replay
from research.simple_full_strategy_discovery_v1.entries import signal_at
from research.simple_full_strategy_discovery_v1.execution import floor_to_valid_tick
from research.simple_full_strategy_discovery_v1.harvest import AUDIT, assert_dev_only_day
from research.simple_full_strategy_discovery_v1.spec import candidate_ids


def test_grid_is_exactly_75():
    ids = candidate_ids()
    assert len(ids) == 75
    assert len(set(ids)) == 75
    assert len(ENTRY_IDS) * len(EXEC_IDS) * len(EXIT_IDS) == int(CANDIDATE_N)
    assert POSITION_CAP == 5
    assert SHARES == 100
    assert WAIT_SEC == 5.0
    assert MAX_RESEARCH_DATE == "20260807"
    assert "20260810" not in DEVELOPMENT_DAYS
    assert "20260828" not in DEVELOPMENT_DAYS
    assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")
    assert len(BURNED_HOLDOUT_DAYS) == 8


def test_e1_first_cross():
    n = 4
    open_ = np.asarray([10.0] * n)
    high = np.asarray([11.0] * n)
    low = np.asarray([9.0] * n)
    close = np.asarray([10.0, 10.5, 11.0, 10.2])
    volume = np.asarray([100.0] * n)
    vwap = np.asarray([10.2] * n)
    s1 = signal_at("E1", open_, high, low, close, volume, vwap, 1)
    s2 = signal_at("E1", open_, high, low, close, volume, vwap, 2)
    assert s1["SIGNAL"] is True
    assert s2["SIGNAL"] is False


def test_e5_momentum():
    open_ = np.asarray([10.0, 10.0, 10.5])
    high = np.asarray([10.4, 10.6, 11.0])
    low = np.asarray([9.8, 9.9, 10.4])
    close = np.asarray([10.1, 10.3, 10.8])
    volume = np.asarray([1.0, 1.0, 1.0])
    vwap = np.asarray([10.0, 10.0, 10.0])
    s1 = signal_at("E5", open_, high, low, close, volume, vwap, 1)
    s2 = signal_at("E5", open_, high, low, close, volume, vwap, 2)
    assert s1["SIGNAL"] is False
    assert s2["SIGNAL"] is True


def test_occupancy_cap_and_same_symbol():
    t0 = 1_000_000.0
    rows = []
    for i in range(6):
        rows.append(
            {
                "date": "20260803",
                "symbol": f"S{i}",
                "t0": t0 + float(i) * 0.01,
                "WOULD_FILL": True,
                "fill_t": t0 + float(i) * 0.01 + 0.1,
                "fill_price": 100.0,
                "exit_t": t0 + 120.0,
                "exit_price": 101.0,
                "pnl_yen_100": 100.0,
                "exit_reason": "Z1",
            }
        )
    occ = portfolio_replay(rows, wait_sec=5.0, position_cap=5)
    assert int(occ["fill_n"]) == 5
    assert int(occ["cap_blocked"]) == 1
    same = [
        {
            "date": "20260803",
            "symbol": "7203",
            "t0": t0,
            "WOULD_FILL": True,
            "fill_t": t0 + 0.1,
            "fill_price": 100.0,
            "exit_t": t0 + 120.0,
            "exit_price": 101.0,
            "pnl_yen_100": 100.0,
            "exit_reason": "Z1",
        },
        {
            "date": "20260803",
            "symbol": "7203",
            "t0": t0 + 1.0,
            "WOULD_FILL": True,
            "fill_t": t0 + 1.1,
            "fill_price": 100.0,
            "exit_t": t0 + 120.0,
            "exit_price": 101.0,
            "pnl_yen_100": 50.0,
            "exit_reason": "Z1",
        },
    ]
    occ2 = portfolio_replay(same, wait_sec=5.0, position_cap=5)
    assert int(occ2["fill_n"]) == 1
    assert int(occ2["same_symbol_blocked"]) == 1


def test_floor_tick():
    assert abs(floor_to_valid_tick(1234.7) - 1234.0) < 1e-12
    assert abs(floor_to_valid_tick(3500.0) - 3500.0) < 1e-12


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


def test_gates_and_rank():
    fail_cov = {"TRADE_N": 10, "TRADING_DAY_WITH_FILL_N": 3, "trades_per_day": 1.0}
    assert coverage_ok(fail_cov) is False
    ok_cov = {"TRADE_N": 40, "TRADING_DAY_WITH_FILL_N": 8, "trades_per_day": 4.0}
    assert coverage_ok(ok_cov) is True
    eco = {
        "TOTAL_PNL": 1000.0,
        "PF": 1.2,
        "positive_day_n": 6,
        "negative_day_n": 4,
        "EX_BEST_DAY_PNL": 100.0,
        "DROP_TOP_SYMBOL_PNL": 50.0,
        "MAXDD": -200.0,
    }
    assert economic_ok(eco) is True
    eco_dd = dict(eco)
    eco_dd["MAXDD"] = -2000.0
    assert economic_ok(eco_dd) is False
    a = {"candidate_id": "E1_X1_Z1", "gate": "PASS", "score": 1.0, "PF": 1.2, "TRADE_N": 40}
    b = {"candidate_id": "E1_X1_Z2", "gate": "PASS", "score": 2.0, "PF": 1.1, "TRADE_N": 40}
    ranked = rank_pass([a, b])
    assert ranked[0]["candidate_id"] == "E1_X1_Z2"
    assert score_of({"TRADE_N": 10, "TOTAL_PNL": 100, "EX_BEST_DAY_PNL": 80, "DROP_TOP_SYMBOL_PNL": 90}) == 8.0


def test_verdicts():
    d = decide(integrity=False, pass_n=1, lodo_pack={"stable": True}, winner={"candidate_id": "x"})
    assert d["CASE"] == "E"
    d = decide(integrity=True, pass_n=0, lodo_pack=None, winner=None)
    assert d["CASE"] == "B"
    assert d["VERDICT"] == CASE_B
    d = decide(integrity=True, pass_n=1, lodo_pack={"stable": False}, winner={"candidate_id": "x"})
    assert d["CASE"] == "C"
    assert d["VERDICT"] == CASE_C
    d = decide(integrity=True, pass_n=1, lodo_pack={"stable": True}, winner={"candidate_id": "x"})
    assert d["CASE"] == "A"
    assert d["VERDICT"] == CASE_A
    assert d["FULL_STRATEGY_DEV_FROZEN"] is True
    assert d["STRESS_OPENED"] is False
    assert d["SIZING"] is False

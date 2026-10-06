"""Measurement decomposition tests. No holdout. No family retune. No Family #3 execution."""
from __future__ import annotations

import pytest

from research.entry_edge_measurement_decomposition_v1 import (
    DEVELOPMENT_DAYS,
    LOCKED_HOLDOUT_DAYS,
    MAX_RESEARCH_DATE,
    NEXT_FAMILY_EXECUTED_THIS_RUN,
    PARAMETER_SEARCH_PERFORMED,
    STRESS_DAYS,
    STRATEGY_SEARCH,
)
from research.entry_edge_measurement_decomposition_v1.analyze import CASE_A, CASE_B, CASE_E, decide, pack_pop
from research.entry_edge_measurement_decomposition_v1.harvest import AUDIT, assert_dev_only_day
from research.entry_edge_measurement_decomposition_v1.quotes import (
    bps_ratio,
    entry_half_spread_bps,
    exit_half_spread_bps,
    mid,
    residual_ask_bid,
    spread_bps,
)


def test_dev_only_bounds():
    assert len(DEVELOPMENT_DAYS) == 10
    assert MAX_RESEARCH_DATE == "20260807"
    assert all(d <= MAX_RESEARCH_DATE for d in DEVELOPMENT_DAYS)
    assert "20260810" not in DEVELOPMENT_DAYS
    assert set(DEVELOPMENT_DAYS).isdisjoint(LOCKED_HOLDOUT_DAYS)
    assert set(DEVELOPMENT_DAYS).isdisjoint(STRESS_DAYS)
    assert STRATEGY_SEARCH is False
    assert PARAMETER_SEARCH_PERFORMED is False
    assert NEXT_FAMILY_EXECUTED_THIS_RUN is False


def test_holdout_stress_blocked():
    AUDIT["LOCKED_HOLDOUT_READ_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    with pytest.raises(RuntimeError, match="LOCKED_HOLDOUT_READ"):
        assert_dev_only_day("20260810")
    assert AUDIT["LOCKED_HOLDOUT_READ_N"] >= 1
    with pytest.raises(RuntimeError, match="STRESS_READ"):
        assert_dev_only_day("20260828")
    AUDIT["FUTURE_DATA_N"] = 0
    with pytest.raises(RuntimeError, match="FUTURE"):
        assert_dev_only_day("20260903")
    AUDIT["LOCKED_HOLDOUT_READ_N"] = 0
    AUDIT["STRESS_READ_N"] = 0
    AUDIT["FUTURE_DATA_N"] = 0


def test_markout_identities():
    bid0, ask0 = 100.0, 100.20
    bidh, askh = 100.10, 100.30
    m0 = mid(bid0, ask0)
    mh = mid(bidh, askh)
    assert m0 == 100.10
    assert abs(spread_bps(bid0, ask0) - (0.20 / 100.10) * 10000) < 1e-9
    mid_m = bps_ratio(mh, m0)
    ask_mid = bps_ratio(mh, ask0)
    ask_bid = bps_ratio(bidh, ask0)
    ehs = entry_half_spread_bps(bid0, ask0)
    xhs = exit_half_spread_bps(bidh, askh)
    res = residual_ask_bid(mid_m, ask_bid, ehs, xhs)
    assert res is not None
    assert abs(res) < 3.0


def test_case_e_integrity():
    d = decide(integrity=False, breakout={}, vwap={}, lift_b={}, lift_v={})
    assert d["CASE"] == "E"
    assert d["VERDICT"] == CASE_E


def test_case_b_both_mid_negative():
    def pack_neg(label: str) -> dict:
        rows = []
        for i, day in enumerate(DEVELOPMENT_DAYS):
            rows.append(
                {
                    "date": day,
                    "symbol": "1000",
                    "eligible": True,
                    "spread0": 20.0,
                    "entry_hs": 10.0,
                    "MID_60": -1.0,
                    "MID_180": -1.0,
                    "MID_300": -1.0,
                    "ASK_MID_60": -11.0,
                    "ASK_MID_180": -11.0,
                    "ASK_MID_300": -11.0,
                    "ASK_BID_60": -21.0,
                    "ASK_BID_180": -21.0,
                    "ASK_BID_300": -21.0,
                    "EXIT_HS_60": 10.0,
                    "EXIT_HS_180": 10.0,
                    "EXIT_HS_300": 10.0,
                    "RESIDUAL_60": 0.0,
                    "RESIDUAL_180": 0.0,
                    "RESIDUAL_300": 0.0,
                }
            )
        return pack_pop(rows, days=list(DEVELOPMENT_DAYS), label=label, signal_n=10)

    b = pack_neg("B")
    v = pack_neg("V")
    d = decide(
        integrity=True,
        breakout=b,
        vwap=v,
        lift_b={"MID_ALPHA_LIFT_180": -1.0, "MID_ALPHA_LIFT_300": -1.0},
        lift_v={"MID_ALPHA_LIFT_180": -1.0, "MID_ALPHA_LIFT_300": -1.0},
    )
    assert d["CASE"] == "B"
    assert d["VERDICT"] == CASE_B
    assert d["NEXT_FAMILY"] == "FAILED_BREAKDOWN_RECLAIM"
    assert d["REVIVE_EXISTING_FAMILIES"] is False


def test_case_a_mid_positive_ask_bid_is_spread():
    rows = []
    for day in DEVELOPMENT_DAYS:
        rows.append(
            {
                "date": day,
                "symbol": "1000",
                "eligible": True,
                "spread0": 20.0,
                "entry_hs": 10.0,
                "MID_60": 2.0,
                "MID_180": 2.0,
                "MID_300": 2.0,
                "ASK_MID_60": -8.0,
                "ASK_MID_180": -8.0,
                "ASK_MID_300": -8.0,
                "ASK_BID_60": -18.0,
                "ASK_BID_180": -18.0,
                "ASK_BID_300": -18.0,
                "EXIT_HS_60": 10.0,
                "EXIT_HS_180": 10.0,
                "EXIT_HS_300": 10.0,
                "RESIDUAL_180": 0.0,
            }
        )
    p = pack_pop(rows, days=list(DEVELOPMENT_DAYS), label="X", signal_n=10)
    d = decide(
        integrity=True,
        breakout=p,
        vwap=p,
        lift_b={"MID_ALPHA_LIFT_180": 2.0, "MID_ALPHA_LIFT_300": 2.0},
        lift_v={"MID_ALPHA_LIFT_180": 2.0, "MID_ALPHA_LIFT_300": 2.0},
    )
    assert d["CASE"] == "A"
    assert d["VERDICT"] == CASE_A
    assert d["REVIVE_EXISTING_FAMILIES"] is False

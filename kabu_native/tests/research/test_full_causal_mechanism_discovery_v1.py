"""FULL_CAUSAL_MECHANISM_DISCOVERY_V1. Mapping freeze, closed tags, operator EXIT, occupancy fill-time."""
from __future__ import annotations

import numpy as np
import pytest

from research.full_causal_mechanism_discovery_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_INTEGRITY,
    CASE_PARITY,
    EXIT_SEARCH,
    NEXT_A,
    NEXT_B,
    NEXT_FIX,
    PORTFOLIO_WAIT_SEC,
    POSITION_CAP,
    RAW_SIGNAL_SCREENING_AS_PRIMARY_DECISION_UNIT,
    REQUIRED_LIBRARY_SHA256,
)
from research.full_causal_mechanism_discovery_v1.analyze import decide, occupancy_rows
from research.full_causal_mechanism_discovery_v1.candidates import freeze_candidate_set
from research.full_causal_mechanism_discovery_v1.exits import o1_fire_i, o2_fire_i, o3_fire_i, post_fill_bars
from research.full_causal_mechanism_discovery_v1.harvest import assert_dev_only_day
from research.full_causal_mechanism_discovery_v1.mapping import freeze_mapping, mapping_sha256
from research.full_causal_mechanism_discovery_v1.publish import SHEET_ORDER
from research.full_causal_mechanism_discovery_v1.spec import library_sha256
from research.simple_tech_entry_family.portfolio import portfolio_replay


def test_library_sha_and_flags():
    assert library_sha256() == REQUIRED_LIBRARY_SHA256
    assert RAW_SIGNAL_SCREENING_AS_PRIMARY_DECISION_UNIT is False
    assert EXIT_SEARCH is False
    freeze = freeze_mapping()
    assert freeze["MAPPING_FROZEN_BEFORE_ECONOMICS"] is True
    assert freeze["EXIT_SEARCH"] is False
    assert freeze["ENTRY_EXIT_GRID"] is False
    assert mapping_sha256() == freeze["FULL_STRATEGY_MAPPING_SHA256"]
    assert mapping_sha256() == mapping_sha256()


def test_candidate_set_closed_and_selectable():
    freeze = freeze_candidate_set()
    assert freeze["FULL_CAUSAL_CANDIDATE_N"] == 42
    assert freeze["CANDIDATE_SET_FROZEN_BEFORE_ECONOMICS"] is True
    assert freeze["CLOSED_REFERENCE_N"] + freeze["SELECTABLE_FULL_CAUSAL_CANDIDATE_N"] == 42
    assert freeze["SELECTABLE_FULL_CAUSAL_CANDIDATE_N"] >= 1
    by = {r["STRATEGY_ID"]: r for r in freeze["candidates"]}
    r2 = [r for r in freeze["candidates"] if r["MECHANISM_ID"] == "O3_RECLAIM_ACCEPT_NEXT__R2"][0]
    assert r2["EXACT_CLOSED_ENTRY_IDENTITY"] is True
    assert r2["SELECTABLE"] is False
    st = [r for r in freeze["candidates"] if r["MECHANISM_ID"] == "O2_HANDOFF_NEXT__S_CLOSE_ABOVE_VWAP__S_RCI_ABOVE_NEG80"][0]
    assert st["EXACT_CLOSED_ENTRY_IDENTITY"] is True
    persist = [r for r in freeze["candidates"] if r["MECHANISM_ID"] == "O1_PERSIST_NEXT__S_MA_TREND_UP"][0]
    assert persist["EXACT_CLOSED_ENTRY_IDENTITY"] is True
    assert persist["CAP"] == 5
    assert persist["EXECUTION_ID"] == "X1_IMMEDIATE_ASK"
    ids = list(by)
    assert len(ids) == len(set(ids))


def test_o1_o2_o3_fire_after_fill_only():
    finalize = np.array([10.0, 70.0, 130.0, 190.0, 250.0], dtype=float)
    fill_t = 80.0
    idxs = post_fill_bars(finalize, fill_t)
    assert idxs == [2, 3, 4]
    p = np.array([True, True, True, False, False])
    assert o1_fire_i(idxs, p) == 3
    assert o1_fire_i(idxs, np.array([False, False, True, True, True])) is None
    q = np.array([True, True, True, True, False])
    assert o2_fire_i(idxs, p, q) == 3
    close = np.array([101.0, 101.0, 101.0, 99.0, 98.0], dtype=float)
    thesis = 100.0
    assert o3_fire_i(idxs, close, thesis) == 3
    later_vwap = 90.0
    assert close[2] > later_vwap
    assert o3_fire_i([2], close, thesis) is None
    assert o3_fire_i(idxs, close, thesis) == 3


def test_occupancy_starts_at_fill_not_signal():
    rows = [
        {
            "date": "20260803",
            "symbol": "A",
            "t0": 100.0,
            "signal_time": 100.0,
            "WOULD_FILL": True,
            "fill_t": 150.0,
            "exit_t": 200.0,
            "fill_price": 10,
            "exit_price": 11,
            "pnl_yen_100": 100.0,
        },
        {
            "date": "20260803",
            "symbol": "B",
            "t0": 110.0,
            "signal_time": 110.0,
            "WOULD_FILL": True,
            "fill_t": 151.0,
            "exit_t": 201.0,
            "fill_price": 10,
            "exit_price": 11,
            "pnl_yen_100": 100.0,
        },
        {
            "date": "20260803",
            "symbol": "C",
            "t0": 90.0,
            "signal_time": 90.0,
            "WOULD_FILL": False,
            "fill_t": None,
            "exit_t": None,
        },
    ]
    occ = occupancy_rows(rows)
    assert len(occ) == 2
    assert occ[0]["t0"] == 150.0
    assert occ[1]["t0"] == 151.0
    got = portfolio_replay(occ, wait_sec=float(PORTFOLIO_WAIT_SEC), position_cap=1)
    assert int(got["cap_blocked"]) == 1
    assert int(got["fill_n"]) == 1
    assert POSITION_CAP == 5


def test_integrity_fail_and_parity_block_economics():
    d = decide(
        integrity_pass=False,
        canary_ok=True,
        economics_opened=False,
        base_qualified=[{"STRATEGY_ID": "X"}],
        closed_qualified=[],
        transfer={"ST_ALL": True},
        full_dev_id="X",
    )
    assert d["VERDICT"] == CASE_INTEGRITY
    assert d["NEXT"] == NEXT_FIX
    assert d["ECONOMICS_OPENED"] is False
    d2 = decide(
        integrity_pass=True,
        canary_ok=False,
        economics_opened=False,
        base_qualified=[{"STRATEGY_ID": "X"}],
        closed_qualified=[],
        transfer={"ST_ALL": True},
        full_dev_id="X",
    )
    assert d2["VERDICT"] == CASE_PARITY
    assert d2["ECONOMICS_OPENED"] is False


def test_decision_cases_a_b_c_d():
    q = [{"STRATEGY_ID": "FC__NEW"}]
    d = decide(
        integrity_pass=True,
        canary_ok=True,
        economics_opened=True,
        base_qualified=q,
        closed_qualified=[],
        transfer={"ST_ALL": True},
        full_dev_id="FC__NEW",
    )
    assert d["VERDICT"] == CASE_A
    assert d["NEXT"] == NEXT_A
    assert d["DEV_CANDIDATE"] == "FC__NEW"
    d_b = decide(
        integrity_pass=True,
        canary_ok=True,
        economics_opened=True,
        base_qualified=q,
        closed_qualified=[],
        transfer={"ST_ALL": False},
        full_dev_id="FC__NEW",
    )
    assert d_b["VERDICT"] == CASE_B
    assert d_b["NEXT"] == NEXT_B
    assert d_b["DEV_CANDIDATE"] is None
    d_c = decide(
        integrity_pass=True,
        canary_ok=True,
        economics_opened=True,
        base_qualified=[],
        closed_qualified=[],
        transfer=None,
        full_dev_id=None,
    )
    assert d_c["VERDICT"] == CASE_C
    d_d = decide(
        integrity_pass=True,
        canary_ok=True,
        economics_opened=True,
        base_qualified=[],
        closed_qualified=[{"STRATEGY_ID": "FC__R2"}],
        transfer=None,
        full_dev_id=None,
    )
    assert d_d["VERDICT"] == CASE_D
    assert d_d["NEXT"] == NEXT_B


def test_holdout_blocked_and_sheet_order():
    with pytest.raises(RuntimeError):
        assert_dev_only_day("20260810")
    with pytest.raises(RuntimeError):
        assert_dev_only_day("20260828")
    with pytest.raises(RuntimeError):
        assert_dev_only_day("20260903")
    with pytest.raises(RuntimeError):
        assert_dev_only_day("20260907")
    assert SHEET_ORDER[0] == "answers"
    assert SHEET_ORDER[-1] == "safety"
    assert "integrity" in SHEET_ORDER
    assert "fold_transfer" in SHEET_ORDER
    assert "causal_ex_top" in SHEET_ORDER

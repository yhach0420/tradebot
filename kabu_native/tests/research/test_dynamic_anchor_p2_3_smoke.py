"""P2-3 smoke: exclusive funnel, state labels, WAIT_SEC=1 ask path. No Capture PnL."""
from __future__ import annotations

import numpy as np

from research.dynamic_anchor_p2_3.fill_path import wait_ask_path
from research.dynamic_anchor_p2_3.metrics import exclusive_entry_terminal, funnel_integrity
from research.dynamic_anchor_p2_3.state import classify_dynamic_state
from small_paper.v1r_primary_runtime import WAIT_SEC


def test_exclusive_entry_split():
    assert exclusive_entry_terminal(
        live_admitted=True, pending_before=True, open_before=True,
        in_elig=True, feature_evaluable=True, score_evaluable=True, joint_admitted=True,
    ) == "ADMITTED"
    assert exclusive_entry_terminal(
        live_admitted=False, pending_before=True, open_before=False,
        in_elig=True, feature_evaluable=True, score_evaluable=True, joint_admitted=True,
    ) == "BLOCKED_PENDING"
    assert exclusive_entry_terminal(
        live_admitted=False, pending_before=False, open_before=True,
        in_elig=True, feature_evaluable=True, score_evaluable=True, joint_admitted=True,
    ) == "BLOCKED_OPEN"
    assert exclusive_entry_terminal(
        live_admitted=False, pending_before=False, open_before=False,
        in_elig=True, feature_evaluable=False, score_evaluable=True, joint_admitted=False,
    ) == "OTHER_REJECT"
    assert exclusive_entry_terminal(
        live_admitted=False, pending_before=False, open_before=False,
        in_elig=True, feature_evaluable=True, score_evaluable=True, joint_admitted=True,
    ) == "BLOCKED_CAP"
    assert exclusive_entry_terminal(
        live_admitted=False, pending_before=False, open_before=False,
        in_elig=True, feature_evaluable=True, score_evaluable=True, joint_admitted=False,
    ) == "NOT_SELECTED"


def test_funnel_integrity_pass():
    got = funnel_integrity({
        "confirmed": 2446,
        "NOT_SELECTED": 55,
        "BLOCKED_OPEN": 3,
        "BLOCKED_PENDING": 0,
        "BLOCKED_CAP": 4,
        "OTHER_REJECT": 0,
        "ADMITTED": 2384,
        "FILLED": 132,
        "EXPIRED": 2252,
    })
    assert got["pass"] is True
    assert got["entry_sum"] == 2446
    assert got["fill_sum"] == 2384


def test_funnel_integrity_fail():
    got = funnel_integrity({
        "confirmed": 10,
        "NOT_SELECTED": 1,
        "BLOCKED_OPEN": 0,
        "BLOCKED_PENDING": 0,
        "BLOCKED_CAP": 0,
        "OTHER_REJECT": 0,
        "ADMITTED": 8,
        "FILLED": 3,
        "EXPIRED": 4,
    })
    assert got["pass"] is False
    assert got["entry_ok"] is False
    assert got["fill_ok"] is False


def test_anchor_active_not_called_profit_taken():
    t0 = 1_000.0
    t1 = t0 + 600.0
    confirms = [{
        "date": "20260722",
        "symbol": "4062",
        "session": "AM",
        "t0": t0,
        "t1": t1,
        "status": "CONFIRMED",
        "reason": None,
    }]
    st = classify_dynamic_state(
        date="20260722", symbol="4062", session="AM",
        signal_t=t0 + 120.0, confirms=confirms,
    )
    assert st["primary_state"] == "ANCHOR_ACTIVE"
    assert st["entry_during_anchor_active"] is True
    assert st["c1_confirmed_before_entry"] is False
    later = classify_dynamic_state(
        date="20260722", symbol="4062", session="AM",
        signal_t=t1 + 5.0, confirms=confirms,
    )
    assert later["primary_state"] == "LAST_C1_CONFIRMED"
    assert later["c1_confirmed_before_entry"] is True
    rej = classify_dynamic_state(
        date="20260722", symbol="4062", session="AM",
        signal_t=t1 + 5.0,
        confirms=[{**confirms[0], "status": "REJECTED", "reason": "SLOPE_OR_ENDPOINT_FAIL"}],
    )
    assert rej["primary_state"] == "LAST_C1_REJECTED"
    assert rej["prior_c1_rejected"] is True
    none = classify_dynamic_state(
        date="20260722", symbol="9999", session="AM", signal_t=t0, confirms=confirms,
    )
    assert none["primary_state"] == "NO_PRIOR_T1_THIS_SESSION"


def test_wait_path_frozen_one_second():
    assert WAIT_SEC == 1.0
    t0 = 10.0
    board = {
        "t": np.asarray([9.0, 10.0, 10.2, 10.6, 11.0, 12.0], dtype=float),
        "ask": np.asarray([101.0, 102.0, 103.0, 100.5, 99.0, 90.0], dtype=float),
        "ask_qty": np.ones(6) * 100.0,
        "special": np.zeros(6, dtype=bool),
        "fresh_sec": np.ones(6),
        "bid": np.ones(6) * 100.0,
    }
    path = wait_ask_path(board, signal_time=t0, limit_bid=100.0, wait_sec=WAIT_SEC)
    assert path["first_ask_after_t1"] == 102.0
    assert path["min_ask_during_wait"] == 99.0
    assert path["last_ask_before_expiry"] == 99.0
    assert path["valid_ask_n"] == 4
    # 12.0 is after t0+WAIT_SEC and must not enter the window
    assert path["min_ask_during_wait"] != 90.0


def test_resolve_admitted_fill_by_time_window():
    from research.dynamic_anchor_p2_3.fill_stage import resolve_admitted_fill_stage

    rows = [
        {"entry_terminal": "ADMITTED", "symbol": "4062", "anchor": "D1.000000", "t1": 10.0, "date": "20260722"},
        {"entry_terminal": "ADMITTED", "symbol": "6981", "anchor": "D2.000000", "t1": 20.0, "date": "20260722"},
        {"entry_terminal": "NOT_SELECTED", "symbol": "3436", "anchor": "D3.000000", "t1": 30.0, "date": "20260722"},
    ]
    fills = [{"symbol": "4062", "anchor": "OTHER", "fill_time": 10.2}]
    resolve_admitted_fill_stage(rows, fills, wait_sec=1.0)
    assert rows[0]["fill_terminal"] == "FILLED"
    assert rows[1]["fill_terminal"] == "EXPIRED"
    assert rows[2]["fill_terminal"] is None
    assert rows[0]["canonical_terminal_outcome"] == "FILLED"
    assert rows[1]["canonical_terminal_outcome"] == "EXPIRED"

"""Day1 cash-participation freeze tests. No sendorder. Day2 primary unchanged."""
from __future__ import annotations

from pathlib import Path

from research.futures_reversal_x_cash_participation_day1_v1 import (
    BINDING_LOSER,
    BINDING_WINNER,
    CASH_LOOKBACK_SEC,
    CASE_A,
    CASE_C,
    CASE_D,
    DAY2_PRIMARY,
    DAY2_PRIMARY_UNCHANGED,
    ENTRY,
    EXIT,
    FROZEN_TRIGGERS,
    MIN_TRUE_N,
    NEW_FUTURES_FEATURES,
    NEXT_FAIL,
    SELECTOR,
    VERDICT_C,
    VERDICT_D,
)
from research.futures_reversal_x_cash_participation_day1_v1.analyze import _classify
from research.futures_reversal_x_cash_participation_day1_v1.engine import cash_flags


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_reversal_x_cash_participation_day1_v1"


def test_frozen_inventory_and_day2():
    assert SELECTOR == "OBSERVED_TRADE_N_180S"
    assert CASH_LOOKBACK_SEC == 180
    assert MIN_TRUE_N == 3
    assert FROZEN_TRIGGERS == (
        "09:07:32",
        "09:21:46",
        "10:03:03",
        "10:17:35",
        "10:24:41",
        "10:32:39",
        "10:48:42",
        "10:53:05",
        "11:04:12",
    )
    assert BINDING_WINNER == "09:21:46"
    assert BINDING_LOSER == "10:32:39"
    assert ENTRY is False
    assert EXIT is False
    assert NEW_FUTURES_FEATURES is False
    assert DAY2_PRIMARY_UNCHANGED is True
    assert DAY2_PRIMARY["substitutions_allowed"] is False


def test_cash_confirm_is_and_of_three_and_cash3_is_spread():
    both_up_relative = cash_flags(market=0.001, top=0.002, bottom=0.0005)
    assert both_up_relative["CASH1"] is True
    assert both_up_relative["CASH2"] is True
    assert both_up_relative["CASH3"] is True
    assert both_up_relative["CASH_CONFIRM"] is True
    still_down = cash_flags(market=-0.001, top=-0.0004, bottom=-0.002)
    assert still_down["CASH1"] is False
    assert still_down["CASH2"] is False
    assert still_down["CASH3"] is True
    assert still_down["CASH_CONFIRM"] is False
    product_would_pass = cash_flags(market=-0.001, top=-0.002, bottom=-0.001)
    assert product_would_pass["CASH3"] is False


def test_same_confirm_state_is_case_c():
    true10 = {"mean": 20.0, "median": 10.0}
    winner = {"CASH_CONFIRM": True, "trigger_hm": "09:21:46"}
    loser = {"CASH_CONFIRM": True, "trigger_hm": "10:32:39"}
    true_rows = [winner, loser, {"CASH_CONFIRM": True}, {"CASH_CONFIRM": True}]
    case, verdict, nxt, flags = _classify(
        [],
        true_rows,
        winner,
        loser,
        true10,
        {"mean": 5.0},
        {"mean": 4.0},
        {"mean": 3.0},
    )
    assert case == CASE_C
    assert verdict == VERDICT_C
    assert flags["CASH_CONFIRM_NOT_SUFFICIENT"] is True
    assert nxt == NEXT_FAIL


def test_sparse_after_reject_is_case_d():
    winner = {"CASH_CONFIRM": True, "trigger_hm": "09:21:46"}
    loser = {"CASH_CONFIRM": False, "trigger_hm": "10:32:39"}
    true_rows = [winner]
    case, verdict, nxt, flags = _classify(
        [],
        true_rows,
        winner,
        loser,
        {"mean": 74.0, "median": 74.0},
        {"mean": None},
        {"mean": 74.0},
        {"mean": 74.0},
    )
    assert case == CASE_D
    assert verdict == VERDICT_D
    assert flags["single_event_only"] is True
    assert flags["rejects_1030"] is True
    assert nxt == NEXT_FAIL
    assert CASE_A != case


def test_no_retune_surface():
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt
        assert "NK_L1_IMB" not in txt
        assert "Top8" not in txt
        assert "CASH4" not in txt
        assert "CASH1 OR" not in txt

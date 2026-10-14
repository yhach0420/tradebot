"""Causal fill rules. Signals stay immediate. The paper book is not the fill."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(ROOT / "results" / "research" / "execution_realism_layer_v1" / "test_residual.json")

from research.execution_realism_layer_v1.model import ExecutionRealismBook, proven_sell


def _sell_ev(**overrides):
    ev = {
        "t": 1000.0,
        "buy1_px": 1244.0,
        "buy1_qty": 100.0,
        "buy1_time_raw": "2026-10-13T09:03:33",
        "buy1_time": 1000.0,
        "bid": 1244.0,
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": False,
    }
    ev.update(overrides)
    return ev


def test_missing_buy1_time_is_unproven():
    decision = proven_sell(_sell_ev(buy1_time_raw=None))
    assert decision["ok"] is False


def test_smaller_displayed_size_can_fill_partly():
    decision = proven_sell(_sell_ev(buy1_qty=40))
    assert decision["ok"] is True
    assert decision["qty"] == 40
    assert decision["px"] == 1244.0


def test_signal_print_is_not_a_buy_and_trigger_print_is_not_a_sell(tmp_path):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    book = ExecutionRealismBook(0.0)
    book.notifier = None
    book.bind_production_schedule(day="20261013", kind="am")
    pos = {
        "exec_state": "BUY_PENDING",
        "signal_t": 100.0,
        "entry_t": 100.0,
        "entry_px": 1247.0,
        "reserve_px": 1247.0,
        "buy_reachable_t": 100.0,
        "remaining_buy": 100.0,
        "filled_qty": 0.0,
        "sold_qty": 0.0,
        "entry_cost": 0.0,
        "exit_proceeds": 0.0,
        "buy_prints": 0,
        "sell_prints": 0,
        "signal_uid": "20261013|AM|4382.T|1432",
        "support": 1250.0,
        "initial_support": 1250.0,
        "exit_reason": None,
    }
    book.open_pos["4382.T"] = pos
    book.quotes["4382.T"] = {
        1: {"t": 100.0, "ask": 1247.0, "ask_qty": 100.0, "quote_ok": True},
        2: {"t": 100.009, "ask": 1246.0, "ask_qty": 100.0, "quote_ok": True},
        3: _sell_ev(t=100.009, buy1_px=1244.0, bid=1244.0, buy1_time=100.009),
        4: _sell_ev(t=100.040, buy1_px=1243.0, bid=1243.0, buy1_time=100.040),
    }
    book._try_fill_buy("4382.T", pos, 1)
    assert pos["filled_qty"] == 0.0
    book._try_fill_buy("4382.T", pos, 2)
    assert pos["exec_state"] == "POSITION_OPEN"
    assert pos["last_buy_px"] == 1246.0
    pos["exec_state"] = "SELL_PENDING"
    pos["sell_reachable_t"] = 100.009
    pos["exit_signal_t"] = 100.009
    book._try_fill_sell("4382.T", pos, 3)
    assert pos["sold_qty"] == 0.0
    book._try_fill_sell("4382.T", pos, 4)
    assert book.closed[0]["exit_px"] == 1243.0
    assert book.closed[0]["qty"] == 100.0
    assert book.closed[0]["pnl_yen"] == -300.0
    assert "4382.T" not in book.open_pos


def test_unproven_ask_size_does_not_fill(tmp_path):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    book = ExecutionRealismBook(0.0)
    book.notifier = None
    book.bind_production_schedule(day="20261013", kind="am")
    pos = {
        "signal_t": 10.0,
        "buy_reachable_t": 10.0,
        "remaining_buy": 100.0,
        "filled_qty": 0.0,
        "buy_prints": 0,
        "entry_cost": 0.0,
        "signal_uid": "u",
    }
    book.quotes["1.T"] = {1: {"t": 11.0, "ask": 500.0, "ask_qty": None, "quote_ok": True}}
    book._try_fill_buy("1.T", pos, 1)
    assert pos["filled_qty"] == 0.0
    assert pos["buy_unproven"] == 1


def test_cancel_before_fill_releases_the_slot_without_pnl(tmp_path):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    book = ExecutionRealismBook(0.0)
    book.notifier = None
    book.bind_production_schedule(day="20261013", kind="am")
    book._sym("4382.T")
    pos = {
        "exec_state": "BUY_PENDING",
        "exec_intercept": "CANCEL",
        "signal_t": 10.0,
        "filled_qty": 0.0,
        "signal_uid": "20261013|AM|4382.T|1432",
        "exit_reason": None,
    }
    book.open_pos["4382.T"] = pos
    book.traded_today.add("4382.T")
    book._schedule_exit(pos, "4382.T", 1, "BREAK_SUPPORT_FAILURE", 10.02, (10.02, 1244.0), [])
    assert "4382.T" not in book.open_pos
    assert book.closed == []
    assert book.cancels[0]["state"] == "BUY_CANCELLED"
    assert "4382.T" not in book.traded_today

"""Regression for the isolated causal paper book. The default owner is unchanged."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(ROOT / "results" / "research" / "paper_execution_correctness_fix_v1" / "test_residual.json")

from research.anchor_timing_robustness.grid import hm_epoch
from research.execution_realism_layer_v1.model import proven_sell
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook, causal_mode_requested


def _book(tmp_path, kind: str = "am") -> PaperExecutionCausalBook:
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    book = PaperExecutionCausalBook()
    book.notifier = None
    book.ledger_path = None
    book.bind_causal_schedule(day="20261013", kind=kind)
    return book


def _arm_symbol(book: PaperExecutionCausalBook, symbol: str, stamp: float, ask: float = 100.0, pre: float = 99.0) -> None:
    st = book._sym(symbol)
    st.n = 1
    st.t[0] = stamp
    st.ok[0] = True
    st.px[0] = ask
    st.ask_px[0] = ask
    st.pre_high[0] = pre
    st.signals = [0]
    st.sig_ptr = 0


def _sell_quote(stamp: float, px: float, qty: float = 100.0, *, special: bool = False, age: float = 0.0) -> dict:
    return {
        "t": stamp,
        "buy1_px": px,
        "buy1_qty": qty,
        "buy1_time_raw": "stamp",
        "buy1_time": stamp - age,
        "bid": px,
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": special,
    }


def test_switch_defaults_off_and_resolver_stays_on_c3():
    os.environ.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)
    assert causal_mode_requested() is False
    from small_paper.paper_session_executor import resolve_paper_session_executor

    exe = resolve_paper_session_executor({"execution_family": "X1_IMMEDIATE_ASK"}, strategy_mode="C3")
    assert type(exe).__name__ == "C3PaperSessionExecutor"
    assert exe.live_order_enabled is False


def test_same_print_does_not_fill_and_hold_starts_at_the_buy(tmp_path):
    book = _book(tmp_path)
    pos = {
        "exec_state": "BUY_PENDING",
        "signal_t": 100.0,
        "entry_t": 100.0,
        "buy_reachable_t": 100.0,
        "remaining_buy": 100.0,
        "filled_qty": 0.0,
        "sold_qty": 0.0,
        "entry_cost": 0.0,
        "exit_proceeds": 0.0,
        "buy_prints": 0,
        "sell_prints": 0,
        "signal_uid": "20261013|AM|4382.T|1432",
    }
    book.open_pos["4382.T"] = pos
    book.quotes["4382.T"] = {
        1: {"t": 100.0, "ask": 1247.0, "ask_qty": 100.0, "quote_ok": True},
        2: {"t": 100.009, "ask": 1246.0, "ask_qty": 100.0, "quote_ok": True},
    }
    book._try_fill_buy("4382.T", pos, 1)
    assert pos["filled_qty"] == 0.0
    book._try_fill_buy("4382.T", pos, 2)
    assert pos["exec_state"] == "POSITION_OPEN"
    pos["exec_state"] = "SELL_PENDING"
    pos["exit_signal_t"] = 100.009
    pos["sell_reachable_t"] = 100.009
    book.quotes["4382.T"][3] = _sell_quote(100.009, 1244.0)
    book.quotes["4382.T"][4] = _sell_quote(100.044, 1243.0)
    book._try_fill_sell("4382.T", pos, 3)
    assert "4382.T" in book.open_pos
    book._try_fill_sell("4382.T", pos, 4)
    closed = book.closed[0]
    assert closed["fill_hold_sec"] == 100.044 - 100.009
    assert closed["proves_broker_fill"] is False
    assert closed["measured_order_latency_sec"] is None
    assert closed["pnl_yen"] == -300.0


def test_exit_signal_is_not_cancelled_when_it_arrives_with_the_buy(tmp_path):
    book = _book(tmp_path)
    book._sym("4382.T")
    pos = {
        "exec_state": "POSITION_OPEN",
        "exec_intercept": "SELL",
        "signal_t": 100.0,
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 124600.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "entry_fill_t": 100.009,
        "signal_uid": "20261013|AM|4382.T|1432",
        "_buy_counted": True,
    }
    book.open_pos["4382.T"] = pos
    book._schedule_exit(pos, "4382.T", 2, "BREAK_SUPPORT_FAILURE", 100.009, (100.009, 1244.0), [])
    assert pos["exec_state"] == "SELL_PENDING"
    assert pos["exit_signal_t"] == 100.009
    assert book.exit_signal_n == 1


def test_pending_buy_reserves_cap_and_same_symbol_blocks(tmp_path):
    book = _book(tmp_path)
    for index in range(5):
        _arm_symbol(book, f"{index}.T", 1_000.0 + index)
        book._on_signal(f"{index}.T", 0, 1_000.0 + index, [], 1_000.0 + index)
    assert len(book.open_pos) == 5
    assert all(pos["exec_state"] == "BUY_PENDING" for pos in book.open_pos.values())
    _arm_symbol(book, "9.T", 2000.0)
    book._on_signal("9.T", 0, 2000.0, [], 2000.0)
    assert "9.T" not in book.open_pos
    assert book.counts["cap"] == 1
    _arm_symbol(book, "0.T", 3000.0)
    book._sym("0.T").signals = [0]
    book._sym("0.T").sig_ptr = 0
    book._on_signal("0.T", 0, 3000.0, [], 3000.0)
    assert book.counts["same_symbol"] == 1


def test_cancel_partial_stale_and_special(tmp_path):
    book = _book(tmp_path)
    book._sym("1.T")
    pending = {
        "exec_state": "BUY_PENDING",
        "exec_intercept": "CANCEL",
        "signal_t": 10.0,
        "filled_qty": 0.0,
        "signal_uid": "cancel",
    }
    book.open_pos["1.T"] = pending
    book._schedule_exit(pending, "1.T", 0, "BREAK_SUPPORT_FAILURE", 11.0, (11.0, 1.0), [])
    assert "1.T" not in book.open_pos
    assert book.cancels[0]["state"] == "BUY_CANCELLED"
    assert book.cancels[0].get("pnl_yen", None) is None

    partial = {
        "exec_state": "BUY_PENDING",
        "signal_t": 10.0,
        "buy_reachable_t": 10.0,
        "remaining_buy": 100.0,
        "filled_qty": 0.0,
        "sold_qty": 0.0,
        "entry_cost": 0.0,
        "exit_proceeds": 0.0,
        "buy_prints": 0,
        "sell_prints": 0,
        "signal_uid": "partial",
    }
    book.open_pos["2.T"] = partial
    book.quotes["2.T"] = {1: {"t": 11.0, "ask": 500.0, "ask_qty": 40.0, "quote_ok": True}}
    book._try_fill_buy("2.T", partial, 1)
    assert partial["exec_state"] == "BUY_PARTIAL"
    assert partial["filled_qty"] == 40.0

    stale = proven_sell(_sell_quote(20.0, 100.0, age=30.0))
    special = proven_sell(_sell_quote(20.0, 100.0, special=True))
    assert stale["ok"] is False
    assert special["ok"] is False


def test_morning_settlement_uses_a_later_bid_and_leaves_a_residual(tmp_path):
    book = _book(tmp_path)
    exit_at = float(hm_epoch("20261013", 11, 25))
    bid_at = exit_at + 5.0
    after = float(hm_epoch("20261013", 11, 30)) + 1.0
    filled = {
        "exec_state": "POSITION_OPEN",
        "signal_t": exit_at - 100.0,
        "entry_fill_t": exit_at - 90.0,
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 36000.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "signal_uid": "flat-fill",
        "support": 354.0,
        "initial_support": 354.0,
        "_buy_counted": True,
        "next_index": 0,
    }
    quiet = {
        "exec_state": "POSITION_OPEN",
        "signal_t": exit_at - 80.0,
        "entry_fill_t": exit_at - 70.0,
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 25000.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "signal_uid": "flat-open",
        "support": 250.0,
        "initial_support": 250.0,
        "_buy_counted": True,
        "next_index": 0,
    }
    book.open_pos["9268.T"] = filled
    book.open_pos["6663.T"] = quiet
    book._sym("9268.T")
    book._sym("6663.T")
    book.quotes["9268.T"] = {0: _sell_quote(bid_at, 360.0)}
    book._ensure_flatten(exit_at)
    assert filled["exec_state"] == "SELL_PENDING"
    assert filled["exit_signal_t"] == exit_at
    assert quiet["exit_reason"] == "SESSION_FLAT"
    book._try_fill_sell("9268.T", filled, 0)
    assert book.closed[0]["signal_uid"] == "flat-fill"
    assert book.closed[0]["exit_px"] == 360.0
    book._try_fill_sell("6663.T", quiet, 0)
    book._close_positions()
    assert book.residuals[0]["signal_uid"] == "flat-open"
    assert book.residuals[0]["pnl_yen"] is None
    assert book.residuals[0]["proves_broker_fill"] is False
    _arm_symbol(book, "NEW.T", after, ask=10.0, pre=9.0)
    book._on_signal("NEW.T", 0, after, [], after)
    assert "NEW.T" not in book.open_pos


def test_afternoon_deadline_does_not_invent_a_fill(tmp_path):
    book = _book(tmp_path, "pm")
    assert book.entry_stop_t == float(hm_epoch("20261013", 15, 20))
    assert book.settlement_end_t == float(hm_epoch("20261013", 15, 25))
    assert book.exchange_end_t == float(hm_epoch("20261013", 15, 30))
    late = float(hm_epoch("20261013", 15, 25))
    pos = {
        "exec_state": "SELL_PENDING",
        "signal_t": late - 100.0,
        "entry_fill_t": late - 90.0,
        "exit_signal_t": late - 60.0,
        "sell_reachable_t": late - 60.0,
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 10000.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "signal_uid": "pm-late",
        "_buy_counted": True,
    }
    book.open_pos["1.T"] = pos
    book.quotes["1.T"] = {0: _sell_quote(late, 100.0)}
    book._try_fill_sell("1.T", pos, 0)
    assert pos["sold_qty"] == 0.0
    book._close_positions()
    assert book.residuals[0]["pnl_yen"] is None
    assert book.closed == []

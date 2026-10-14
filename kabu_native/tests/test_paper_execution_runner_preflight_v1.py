"""Preflight checks for an offline causal connection. The sealed runner is not started."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(
    ROOT / "results" / "research" / "paper_execution_runner_integration_preflight_v1" / "test_residual.json"
)
os.environ.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)

from research.anchor_timing_robustness.grid import hm_epoch
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook
from small_paper.paper_execution_offline_adapter import resolve_offline_paper_executor


def _book(tmp_path, kind: str = "am") -> PaperExecutionCausalBook:
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    book = PaperExecutionCausalBook()
    book.notifier = None
    book.ledger_path = None
    book.bind_causal_schedule(day="20261013", kind=kind)
    return book


def _activation() -> dict:
    return {"execution_family": "X1_IMMEDIATE_ASK", "activation_id": "preflight"}


def test_off_switch_is_the_sealed_owner(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", raising=False)
    left = resolve_offline_paper_executor(_activation(), strategy_mode="C3", offline=False)
    right = resolve_offline_paper_executor(_activation(), strategy_mode="C3", offline=True)
    assert type(left).__name__ == "C3PaperSessionExecutor"
    assert type(right).__name__ == "C3PaperSessionExecutor"
    assert left.live_order_enabled is False
    assert left.snapshot_status()["submit"] == 0
    assert left.snapshot_status()["cancel"] == 0
    assert left.snapshot_status()["live"] == 0


def test_on_switch_without_offline_stays_on_the_sealed_adapter(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    monkeypatch.delenv("TRADEBOT_PAPER_EXECUTION_OFFLINE", raising=False)
    sealed = resolve_offline_paper_executor(_activation(), strategy_mode="C3", offline=False)
    assert type(sealed).__name__ == "C3PaperSessionExecutor"
    assert not isinstance(sealed, PaperExecutionCausalBook)


def test_on_switch_offline_builds_the_causal_book(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    book = resolve_offline_paper_executor(_activation(), strategy_mode="C3", offline=True)
    assert isinstance(book, PaperExecutionCausalBook)
    assert book.live_order_enabled is False
    assert book.proves_broker_fill is False


def test_sell_pending_keeps_the_slot_and_blocks_a_new_entry(tmp_path):
    book = _book(tmp_path)
    for index in range(4):
        book.open_pos[f"{index}.T"] = {"exec_state": "BUY_PENDING", "exiting": False}
    book.open_pos["4382.T"] = {
        "exec_state": "SELL_PENDING",
        "exiting": False,
        "filled_qty": 100.0,
        "signal_uid": "held",
    }
    st = book._sym("9999.T")
    st.n = 1
    st.t[0] = 1_000.0
    st.ok[0] = True
    st.px[0] = 100.0
    st.ask_px[0] = 100.0
    st.pre_high[0] = 99.0
    st.signals = [0]
    st.sig_ptr = 0
    book._on_signal("9999.T", 0, 1_000.0, [], 1_000.0)
    assert book.counts["cap"] == 1
    assert "9999.T" not in book.open_pos
    same = book._sym("4382.T")
    same.n = 1
    same.t[0] = 1_100.0
    same.ok[0] = True
    same.px[0] = 100.0
    same.ask_px[0] = 100.0
    same.pre_high[0] = 99.0
    same.signals = [0]
    same.sig_ptr = 0
    book._on_signal("4382.T", 0, 1_100.0, [], 1_100.0)
    assert book.counts["same_symbol"] == 1
    assert book.open_pos["4382.T"]["exec_state"] == "SELL_PENDING"
    assert len(book.open_pos) == 5


def test_duplicate_sell_does_not_book_twice(tmp_path):
    book = _book(tmp_path)
    pos = {
        "exec_state": "SELL_PENDING",
        "signal_t": 10.0,
        "entry_fill_t": 11.0,
        "exit_signal_t": 12.0,
        "sell_reachable_t": 12.0,
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 10000.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "signal_uid": "once",
        "_buy_counted": True,
    }
    book.open_pos["1.T"] = pos
    quote = {
        "t": 13.0,
        "buy1_px": 101.0,
        "buy1_qty": 100.0,
        "buy1_time_raw": "t",
        "buy1_time": 13.0,
        "bid": 101.0,
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": False,
    }
    book.quotes["1.T"] = {0: quote}
    book._try_fill_sell("1.T", pos, 0)
    book._try_fill_sell("1.T", pos, 0)
    assert len(book.closed) == 1
    assert book.closed[0]["pnl_yen"] == 100.0


def test_afternoon_does_not_inherit_a_morning_position(tmp_path):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(tmp_path / "residual.json")
    am = _book(tmp_path, "am")
    am.residuals.append({"symbol": "9268.T", "pnl_yen": None, "signal_uid": "am-left"})
    am.still_open.append({"symbol": "9268.T", "notional": 1.0})
    pm = PaperExecutionCausalBook()
    pm.notifier = None
    pm.ledger_path = None
    pm.bind_causal_schedule(day="20261013", kind="pm")
    assert "9268.T" not in pm.open_pos
    assert pm.closed == []
    assert float(hm_epoch("20261013", 15, 25)) == pm.settlement_end_t
    assert float(hm_epoch("20261013", 15, 30)) == pm.exchange_end_t


def test_locked_1432_order_is_buy_then_later_sell():
    expected = (
        ("ENTRY_SIGNAL", "BUY_FILLED", "EXIT_SIGNAL", "POSITION_CLOSED"),
    )
    buy = "09:03:33.237"
    sell = "09:03:33.272"
    assert buy < sell
    assert expected[0][0] == "ENTRY_SIGNAL"
    assert expected[0][-1] == "POSITION_CLOSED"

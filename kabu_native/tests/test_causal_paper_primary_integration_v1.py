"""The live paper launcher path, not the offline adapter.

Flags stay off unless a test sets them. No broker and no ingress process.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(
    ROOT / "results" / "research" / "paper_runtime_full_adoption_audit_v1" / "causal_e2e_residual.json"
)
os.environ.pop("TRADEBOT_PAPER_EXECUTION_CAUSAL", None)
os.environ.pop("TRADEBOT_PAPER_EXECUTION_OFFLINE", None)

from research.anchor_timing_robustness.grid import hm_epoch
from small_paper.c3_paper_session import C3PaperSessionExecutor
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook
from small_paper.paper_runner_settlement_v1 import (
    causal_paper_enabled,
    feed_session_end,
    prepare_live_paper_execution_env,
    runner_close_action,
)
from small_paper.paper_session_executor import (
    resolve_paper_session_executor,
    resolve_sealed_session_executor,
)

DAY = "20261013"
ACTIVATION = {"execution_family": "X1_IMMEDIATE_ASK", "activation_id": "V20", "sha256": "0" * 64}


def _epoch(hour: int, minute: int, second: float = 0.0) -> float:
    return float(hm_epoch(DAY, hour, minute)) + float(second)


def _arm(book, symbol: str, stamp: float, ask: float = 100.0, pre: float = 99.0) -> None:
    st = book._sym(symbol)
    st.n = 1
    st.t[0] = stamp
    st.ok[0] = True
    st.px[0] = ask
    st.ask_px[0] = ask
    st.pre_high[0] = pre
    st.signals = [0]
    st.sig_ptr = 0


def _quiet(book) -> None:
    book.notifier = None
    book.ledger_path = None


def test_flag_off_matches_the_immediate_c3_book(monkeypatch, tmp_path):
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "0")
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_OFFLINE", "1")
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    armed = prepare_live_paper_execution_env()
    assert armed["paper_causal"] == "0"
    assert "TRADEBOT_PAPER_EXECUTION_OFFLINE" not in os.environ
    assert causal_paper_enabled() is False
    assert feed_session_end("11:25", kind="am") == "11:25"
    assert runner_close_action("am", day=DAY, now_epoch=_epoch(11, 26)) == "legacy"
    exe = resolve_paper_session_executor(ACTIVATION, strategy_mode="C3")
    _quiet(exe)
    assert type(exe).__name__ == "C3PaperSessionExecutor"
    assert not isinstance(exe, PaperExecutionCausalBook)
    exe.bind_production_schedule(day=DAY, kind="am")
    stamp = _epoch(9, 5)
    _arm(exe, "1.T", stamp, ask=100.0)
    exe._on_signal("1.T", 0, stamp, [], stamp)
    pos = exe.open_pos["1.T"]
    assert pos.get("exec_state") in (None, "")
    assert pos["entry_t"] == stamp
    assert pos["entry_px"] == 100.0
    assert exe.snapshot_status()["submit"] == 0
    assert exe.snapshot_status()["cancel"] == 0
    assert exe.snapshot_status()["live"] == 0


def test_live_launcher_arms_causal_and_drops_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("KABU_PAPER_RUNTIME", "1")
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_OFFLINE", "1")
    monkeypatch.delenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", raising=False)
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    armed = prepare_live_paper_execution_env()
    assert armed == {"paper_causal": "1", "offline": ""}
    assert os.environ.get("TRADEBOT_PAPER_EXECUTION_OFFLINE") is None
    from small_paper.paper_session_executor import bind_selected_live_executor

    binding = bind_selected_live_executor()
    exe = binding["executor"]
    assert binding["session_owner_class"] == "PaperExecutionCausalBook"
    assert binding["execution_mode"] == "PAPER_EXECUTION_CAUSAL_V1"
    assert binding["execution_family"] == "X1_IMMEDIATE_ASK"
    assert binding["x1"] is True
    assert binding["boot_v1r_native_entry"] == 0
    assert isinstance(exe, PaperExecutionCausalBook)
    assert isinstance(exe, C3PaperSessionExecutor)
    assert exe.live_order_enabled is False
    assert exe.snapshot_status()["submit"] == 0
    assert exe.snapshot_status()["cancel"] == 0
    assert exe.snapshot_status()["live"] == 0


def test_runner_source_uses_the_paper_clock_not_the_offline_flag():
    native = ROOT / "src" / "small_paper"
    pilot = (native / "pilot_runner.py").read_text(encoding="utf-8")
    launcher = (native / "v1r_paper_primary_launcher.py").read_text(encoding="utf-8")
    hook = pilot.index("runner_close_action")
    stop = pilot.index("am_pm_policy.force_close_due()", hook)
    assert hook < stop
    assert "feed_session_end" in pilot
    live = launcher[launcher.index("def launch_primary"):]
    assert live.index("prepare_live_paper_execution_env") < live.index("bind_selected_live_executor")


def test_signal_is_not_a_fill_and_same_bid_does_not_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    monkeypatch.delenv("TRADEBOT_PAPER_EXECUTION_OFFLINE", raising=False)
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    book = resolve_paper_session_executor(ACTIVATION, strategy_mode="C3")
    _quiet(book)
    book.bind_causal_schedule(day=DAY, kind="am")
    stamp = _epoch(9, 5)
    _arm(book, "4382.T", stamp, ask=1247.0, pre=1240.0)
    book._on_signal("4382.T", 0, stamp, [], stamp)
    pos = book.open_pos["4382.T"]
    assert pos["exec_state"] == "BUY_PENDING"
    assert pos["filled_qty"] == 0.0
    assert pos["entry_t"] == stamp
    book.quotes["4382.T"] = {
        1: {"t": stamp, "ask": 1247.0, "ask_qty": 100.0, "quote_ok": True},
        2: {"t": stamp + 0.009, "ask": 1246.0, "ask_qty": 100.0, "quote_ok": True},
    }
    book._try_fill_buy("4382.T", pos, 1)
    assert pos["filled_qty"] == 0.0
    book._try_fill_buy("4382.T", pos, 2)
    assert pos["exec_state"] == "POSITION_OPEN"
    assert pos["entry_fill_t"] == stamp + 0.009
    book._schedule_exit(pos, "4382.T", 2, "BREAK_SUPPORT_FAILURE", stamp + 0.009, (stamp + 0.009, 1244.0), [])
    assert pos["exec_state"] == "SELL_PENDING"
    assert "4382.T" in book.open_pos
    book.quotes["4382.T"][3] = {
        "t": stamp + 0.009,
        "buy1_px": 1244.0,
        "buy1_qty": 100.0,
        "buy1_time_raw": "same",
        "buy1_time": stamp + 0.009,
        "bid": 1244.0,
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": False,
    }
    book.quotes["4382.T"][4] = {
        "t": stamp + 0.044,
        "buy1_px": 1243.0,
        "buy1_qty": 100.0,
        "buy1_time_raw": "later",
        "buy1_time": stamp + 0.044,
        "bid": 1243.0,
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": False,
    }
    book._try_fill_sell("4382.T", pos, 3)
    assert book.open_pos["4382.T"]["exec_state"] == "SELL_PENDING"
    book._try_fill_sell("4382.T", pos, 4)
    assert "4382.T" not in book.open_pos
    assert book.closed[0]["pnl_yen"] == -300.0
    assert book.closed[0]["proves_broker_fill"] is False
    assert book.snapshot_status()["submit"] == 0


def test_cap_gross_same_symbol_occupancy_and_reentry(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    book = resolve_paper_session_executor(ACTIVATION, strategy_mode="C3")
    _quiet(book)
    book.bind_causal_schedule(day=DAY, kind="am")
    for index in range(5):
        _arm(book, f"{index}.T", _epoch(9, 5) + index)
        book._on_signal(f"{index}.T", 0, _epoch(9, 5) + index, [], _epoch(9, 5) + index)
    assert len(book.open_pos) == 5
    assert all(pos["exec_state"] == "BUY_PENDING" for pos in book.open_pos.values())
    _arm(book, "9.T", _epoch(9, 6))
    book._on_signal("9.T", 0, _epoch(9, 6), [], _epoch(9, 6))
    assert book.counts["cap"] == 1
    book.open_pos["0.T"]["exiting"] = True
    book.open_pos["0.T"]["exec_state"] = "SELL_PENDING"
    _arm(book, "0.T", _epoch(9, 7))
    book._on_signal("0.T", 0, _epoch(9, 7), [], _epoch(9, 7))
    assert book.counts["occupancy"] == 1
    assert book.open_pos["0.T"]["exec_state"] == "SELL_PENDING"
    book.open_pos.clear()
    _arm(book, "8.T", _epoch(9, 8), ask=20000.0, pre=19900.0)
    book._on_signal("8.T", 0, _epoch(9, 8), [], _epoch(9, 8))
    assert book.gross_cap_denied_n == 1
    assert "8.T" not in book.open_pos
    _arm(book, "7.T", _epoch(9, 9))
    book._on_signal("7.T", 0, _epoch(9, 9), [], _epoch(9, 9))
    assert book.open_pos["7.T"]["exec_state"] == "BUY_PENDING"
    book.open_pos.pop("7.T")
    _arm(book, "7.T", _epoch(9, 10))
    book._on_signal("7.T", 0, _epoch(9, 10), [], _epoch(9, 10))
    assert book.counts["reentry"] == 1
    assert book.counts["same_symbol"] == 0


def test_am_pm_settlement_and_unfilled_residual(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(tmp_path / "residual.json"))
    assert runner_close_action("am", day=DAY, now_epoch=_epoch(11, 19, 59)) == "continue"
    assert runner_close_action("am", day=DAY, now_epoch=_epoch(11, 20)) == "hold"
    assert runner_close_action("am", day=DAY, now_epoch=_epoch(11, 25)) == "hold"
    assert runner_close_action("am", day=DAY, now_epoch=_epoch(11, 30)) == "stop"
    assert runner_close_action("pm", day=DAY, now_epoch=_epoch(15, 19)) == "continue"
    assert runner_close_action("pm", day=DAY, now_epoch=_epoch(15, 20)) == "hold"
    assert runner_close_action("pm", day=DAY, now_epoch=_epoch(15, 25)) == "stop"
    assert feed_session_end("11:25", kind="am") == "11:30"
    assert feed_session_end("15:25", kind="pm") == "15:25"
    book = resolve_paper_session_executor(ACTIVATION, strategy_mode="C3")
    _quiet(book)
    book.bind_causal_schedule(day=DAY, kind="am")
    book.open_pos["1.T"] = {
        "exec_state": "SELL_PENDING",
        "signal_t": _epoch(11, 0),
        "entry_fill_t": _epoch(11, 1),
        "exit_signal_t": _epoch(11, 25),
        "sell_reachable_t": _epoch(11, 25),
        "filled_qty": 100.0,
        "sold_qty": 0.0,
        "entry_cost": 10000.0,
        "exit_proceeds": 0.0,
        "buy_prints": 1,
        "sell_prints": 0,
        "entry_px": 100.0,
        "signal_uid": "quiet",
        "_buy_counted": True,
    }
    book.hold_for_settlement()
    _arm(book, "9.T", _epoch(11, 26))
    book._on_signal("9.T", 0, _epoch(11, 26), [], _epoch(11, 26))
    assert "9.T" not in book.open_pos
    book._close_positions()
    assert book.closed == []
    assert book.residuals[0]["pnl_yen"] is None
    assert book.residuals[0]["signal_uid"] == "quiet"
    again = list(book.residuals)
    book._close_positions()
    assert book.residuals == again


def test_residual_reload_and_offline_adapter_stay_separate(monkeypatch, tmp_path):
    path = tmp_path / "residual.json"
    row = {
        "uid": "20261013|AM|1.T|1",
        "symbol": "1.T",
        "entry_t": _epoch(9, 5),
        "entry_px": 100.0,
        "support": 99.0,
        "pnl_yen": None,
    }
    path.write_text(json.dumps({"strategy_mode": "C3", "residuals": [row]}), encoding="utf-8")
    monkeypatch.setenv("TRADEBOT_C3_RESIDUAL_PATH", str(path))
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_CAUSAL", "1")
    monkeypatch.setenv("TRADEBOT_PAPER_EXECUTION_OFFLINE", "1")
    sealed = resolve_sealed_session_executor(ACTIVATION, strategy_mode="C3")
    assert type(sealed).__name__ == "C3PaperSessionExecutor"
    paper = resolve_paper_session_executor(ACTIVATION, strategy_mode="C3")
    _quiet(paper)
    paper.bind_production_schedule(day=DAY, kind="am")
    assert paper.open_pos["1.T"]["carried"] is True
    assert paper.snapshot_status()["live"] == 0

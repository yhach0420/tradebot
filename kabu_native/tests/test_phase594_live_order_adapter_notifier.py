"""Phase594 LiveOrderAdapter + LiveOrderNotifier tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from small_paper.live_order_adapter import (
    AdapterState,
    LiveOrderAdapterSession,
    SymbolAdapterTrack,
    live_order_adapter_enabled,
    phase594_preflight_check,
    process_paper_entry,
    process_paper_exit,
    run_demo_scenarios,
    _guard_sendorder,
)
from small_paper.live_order_notifier import (
    EVENT_CAPITAL_CHECK_BLOCK,
    EVENT_ORDER_PREPARED,
    EVENT_ORDER_WOULD_SEND,
    EVENT_SAFE_STOP,
    LiveOrderNotifier,
    format_discord_message,
)
from small_paper.live_capital_manager import LiveCapitalSnapshot, evaluate_entry_capital
from small_paper.live_writer import LiveSessionWriter


class _Cfg:
    order_enabled = False
    live_trading_enabled = False
    dry_run = True
    live_order_adapter_enabled = True
    live_order_notifier_enabled = True
    live_order_discord_enabled = False
    live_order_jsonl_enabled = True
    live_capital_check_enabled = True
    max_concurrent_positions = 5
    daily_loss_guard_enabled = False


def _writer(tmp: Path) -> LiveSessionWriter:
    return LiveSessionWriter(tmp, incremental=True, event_fields=["x"])


def test_capital_pass_to_would_send():
    session = LiveOrderAdapterSession()
    with tempfile.TemporaryDirectory() as td:
        w = _writer(Path(td))
        r = process_paper_entry(
            session,
            symbol="6981.T",
            trade={"entry_time": "2026-06-18T09:05:00+09:00"},
            payload={"AskPrice": 2768.0},
            timestamp="2026-06-18T09:05:00+09:00",
            writer=w,
            config=_Cfg(),
        )
    assert r.get("ok") is True
    types = [e["event_type"] for e in session.notifier.events]
    assert EVENT_ORDER_PREPARED in types
    assert EVENT_ORDER_WOULD_SEND in types
    assert session.tracks["6981.T"].state == AdapterState.OPEN_POSITION_DRYRUN


def test_capital_block_no_would_send():
    session = LiveOrderAdapterSession()
    snap = LiveCapitalSnapshot.mock(stock_wallet=20_000, margin_wallet=0)
    block = evaluate_entry_capital(snap, symbol="6981.T", entry_price=2768.0, cap_limit=2)
    assert block["can_enter"] is False
    session.notifier.emit(
        EVENT_CAPITAL_CHECK_BLOCK,
        {**block, "symbol": "6981.T", "side": "ENTRY"},
        writer=MagicMock(),
        config=_Cfg(),
    )
    assert session.would_send_count == 0
    assert any(e["event_type"] == EVENT_CAPITAL_CHECK_BLOCK for e in session.notifier.events)


def test_notifier_exception_does_not_propagate():
    n = LiveOrderNotifier()
    bad_writer = MagicMock()
    bad_writer.append_live_order_event.side_effect = OSError("disk")
    n.emit(
        "ENTRY_SIGNAL",
        {"symbol": "6981.T"},
        writer=bad_writer,
        config=_Cfg(),
    )
    assert n.error_count >= 1


def test_discord_failure_does_not_propagate():
    n = LiveOrderNotifier()

    def boom(_msg: str) -> None:
        raise RuntimeError("discord down")

    cfg = _Cfg()
    cfg.live_order_discord_enabled = True
    n.emit(
        EVENT_ORDER_WOULD_SEND,
        {"symbol": "6981.T", "qty": 100, "price": 2768},
        writer=MagicMock(),
        config=cfg,
        discord_send=boom,
    )


def test_order_enabled_false_no_sendorder():
    session = LiveOrderAdapterSession()
    with tempfile.TemporaryDirectory() as td:
        process_paper_entry(
            session,
            symbol="6981.T",
            trade={"entry_time": "2026-06-18T09:05:00+09:00"},
            payload={"AskPrice": 2768.0},
            timestamp="2026-06-18T09:05:00+09:00",
            writer=_writer(Path(td)),
            config=_Cfg(),
        )
    payload = session.tracks["6981.T"].last_payload
    assert payload is not None
    assert payload.get("would_send") is True
    assert payload.get("dry_run") is True


def test_order_enabled_true_preflight_fail():
    cfg = _Cfg()
    cfg.order_enabled = True
    ok, msg = phase594_preflight_check(cfg)
    assert ok is False
    assert "order_enabled" in msg


def test_exit_stop_market_repay_payload():
    session = LiveOrderAdapterSession()
    session.tracks["6981.T"] = SymbolAdapterTrack(
        symbol="6981.T", state=AdapterState.OPEN_POSITION_DRYRUN, paper_trade_id="6981.T:x"
    )
    with tempfile.TemporaryDirectory() as td:
        r = process_paper_exit(
            session,
            symbol="6981.T",
            context={"exit_reason": "hard_stop", "exit_price": 2700.0},
            timestamp="2026-06-18T09:30:00+09:00",
            writer=_writer(Path(td)),
            config=_Cfg(),
        )
    assert r is not None
    assert r.get("order_type") == "market"
    assert session.tracks["6981.T"].last_payload.get("FrontOrderType") == 10


def test_safe_stop_event():
    session = LiveOrderAdapterSession()
    session.trigger_safe_stop("position_mismatch")
    with tempfile.TemporaryDirectory() as td:
        r = process_paper_entry(
            session,
            symbol="6981.T",
            trade={},
            payload={"AskPrice": 2768.0},
            timestamp="2026-06-18T09:05:00+09:00",
            writer=_writer(Path(td)),
            config=_Cfg(),
        )
    assert r.get("blocked") is True
    assert any(e["event_type"] == EVENT_SAFE_STOP for e in session.notifier.events)


def test_duplicate_symbol_block_message():
    msg = format_discord_message(
        EVENT_CAPITAL_CHECK_BLOCK,
        {
            "symbol": "6981.T",
            "required_margin": 138400,
            "available_margin": 100000,
            "reject_reason": "duplicate_symbol",
        },
    )
    assert "6981.T" in msg
    assert "duplicate_symbol" in msg


def test_jsonl_failure_runtime_continues():
    session = LiveOrderAdapterSession()
    w = MagicMock()
    w.append_live_order_event.side_effect = OSError("fail")
    w.append_live_order_state.side_effect = OSError("fail")
    r = process_paper_entry(
        session,
        symbol="6981.T",
        trade={"entry_time": "2026-06-18T09:05:00+09:00"},
        payload={"AskPrice": 2768.0},
        timestamp="2026-06-18T09:05:00+09:00",
        writer=w,
        config=_Cfg(),
    )
    assert r.get("ok") is True


def test_order_enabled_guard_raises_not_implemented():
    cfg = _Cfg()
    cfg.order_enabled = True
    assert live_order_adapter_enabled(cfg) is False
    with pytest.raises(NotImplementedError):
        _guard_sendorder(cfg)


def test_demo_scenarios():
    with tempfile.TemporaryDirectory() as td:
        rows = run_demo_scenarios(writer=_writer(Path(td)), config=_Cfg())
    assert len(rows) >= 4

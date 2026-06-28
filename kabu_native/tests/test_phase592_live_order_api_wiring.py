"""Phase592 live order API wiring tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from small_paper.live_order_api_wiring import (
    CASH_MARGIN_NEW,
    CASH_MARGIN_REPAY,
    MARGIN_TRADE_DAY,
    LiveOrderWiringSession,
    build_entry_sendorder_payload,
    build_exit_sendorder_payload,
    latency_summary,
    process_entry_wiring,
    process_exit_wiring,
    run_live_order_preflight,
    simulate_stop_exit_emergency_cases,
    wiring_enabled,
)
from small_paper.live_writer import LiveSessionWriter


class _Cfg:
    live_order_api_wiring_enabled = True
    live_order_dry_run_enabled = True
    live_trading_enabled = False
    order_enabled = False
    live_order_entry_timeout_sec = 4.0


def test_wiring_disabled_when_order_enabled():
    cfg = _Cfg()
    cfg.order_enabled = True
    assert wiring_enabled(cfg) is False


def test_entry_payload_fields():
    p = build_entry_sendorder_payload(symbol="7203.T", exchange=1, limit_price=2851.0)
    assert p["CashMargin"] == CASH_MARGIN_NEW
    assert p["MarginTradeType"] == MARGIN_TRADE_DAY
    assert p["Qty"] == 100
    assert p["dry_run"] is True


def test_exit_stop_uses_market():
    p = build_exit_sendorder_payload(symbol="7203.T", exchange=1, exit_reason="hard_stop")
    assert p["CashMargin"] == CASH_MARGIN_REPAY
    assert p["FrontOrderType"] == 10


def test_entry_wiring_latency_under_target():
    session = LiveOrderWiringSession()
    with tempfile.TemporaryDirectory() as td:
        writer = LiveSessionWriter(Path(td), incremental=True, event_fields=["x"])
        process_entry_wiring(
            session,
            symbol="7203.T",
            trade={"entry_time": "2026-06-18T09:05:00+09:00"},
            payload={"AskPrice": 2851.0},
            writer=writer,
            config=_Cfg(),
            entry_signal_ts="2026-06-18T09:05:00+09:00",
        )
    summ = latency_summary(session.latency_samples)
    assert summ["entry"]["p95_ms"] <= 1500


def test_stop_exit_emergency_cases_cover_a_to_e():
    cases = simulate_stop_exit_emergency_cases()
    ids = {c["case_id"] for c in cases}
    assert ids == {"A", "B", "C", "D", "E"}


def test_preflight_blocks_when_order_enabled():
    cfg = _Cfg()
    cfg.order_enabled = True
    rep = run_live_order_preflight(config=cfg, repo_root=Path("."))
    assert not rep.ready

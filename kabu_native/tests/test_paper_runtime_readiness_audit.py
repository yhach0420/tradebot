"""Paper runtime readiness — hook fault isolation tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from small_paper.live_capital_manager import (
    LiveCapitalManagerSession,
    check_entry_capital_on_paper_accept,
    fetch_live_capital_snapshot,
)
from small_paper.live_order_dry_run_adapter import dry_run_adapter_enabled
from small_paper.live_order_api_wiring import wiring_enabled
from small_paper.live_capital_manager import capital_manager_enabled


def test_wiring_and_capital_off_when_order_enabled():
    class C:
        live_order_dry_run_enabled = True
        live_order_api_wiring_enabled = True
        live_capital_check_enabled = True
        order_enabled = True
        live_trading_enabled = False

    assert not wiring_enabled(C())
    assert not capital_manager_enabled(C())


def test_capital_hook_swallowed_in_pilot_runner():
    from small_paper import pilot_runner
    import small_paper.live_capital_manager as lcm

    class Ctx:
        state = type(
            "S",
            (),
            {
                "live_capital_manager": object(),
                "live_capital_read_client": object(),
                "live_capital_api_token": "t",
            },
        )()
        config = MagicMock(order_enabled=False, live_trading_enabled=False)
        gate = MagicMock(state=MagicMock(day_pnl={}))
        writer = MagicMock()

    orig = lcm.check_entry_capital_on_paper_accept
    lcm.check_entry_capital_on_paper_accept = MagicMock(side_effect=RuntimeError("fail"))
    try:
        pilot_runner._maybe_record_live_capital_check_entry(
            Ctx(), sym="7203.T", trade={}, payload={}, acc={}
        )
    finally:
        lcm.check_entry_capital_on_paper_accept = orig


def test_api_offline_snapshot():
    client = MagicMock()
    client.get_wallet_cash.side_effect = OSError("offline")
    snap = fetch_live_capital_snapshot(client, token="x")
    assert snap.api_online is False


def test_jsonl_failure_still_returns_row():
    writer = MagicMock()
    writer.append_live_capital_check.side_effect = OSError("disk")
    client = MagicMock()
    client.get_wallet_cash.return_value = ({"StockAccountWallet": 20000}, 1.0)
    client.get_wallet_margin.return_value = ({"MarginAccountWallet": 0}, 1.0)
    client.get_positions.return_value = ([], 1.0)
    client.get_orders.return_value = ([], 1.0)
    session = LiveCapitalManagerSession(position_cap=5)
    row = check_entry_capital_on_paper_accept(
        session,
        symbol="7203.T",
        trade={},
        payload={"AskPrice": 2768.0},
        writer=writer,
        config=MagicMock(
            order_enabled=False,
            live_trading_enabled=False,
            live_capital_check_enabled=True,
            max_concurrent_positions=5,
            daily_loss_guard_enabled=False,
        ),
        client=client,
        token="t",
    )
    assert row.get("reject_reason") == "insufficient_margin_or_buying_power"

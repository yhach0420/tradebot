"""Phase593 Live Capital Manager tests."""

from __future__ import annotations

from small_paper.live_capital_manager import (
    LiveCapitalSnapshot,
    compute_buying_power,
    compute_required_margin,
    count_cap_slots,
    evaluate_entry_capital,
    min_equity_for_cap,
    open_symbols,
    operational_cap_ok,
)


def test_required_margin_formula():
    assert compute_required_margin(2768.0) == 138400.0


def test_buying_power_matches_phase592b():
    assert compute_buying_power(equity=1_500_000, gross=323_000, leverage=2.0) == 2_677_000.0


def test_cap_reject_separate_from_margin():
    snap = LiveCapitalSnapshot.mock(stock_wallet=1_500_000, margin_wallet=1_500_000, cap_used=5)
    row = evaluate_entry_capital(snap, symbol="7203.T", entry_price=2768.0, cap_limit=5)
    assert row["can_enter"] is False
    assert row["reject_reason"] == "max_concurrent_positions"


def test_margin_wallet_zero_rejects():
    snap = LiveCapitalSnapshot.mock(stock_wallet=20_000, margin_wallet=0.0)
    row = evaluate_entry_capital(snap, symbol="7203.T", entry_price=2768.0, cap_limit=2)
    assert row["can_enter"] is False
    assert row["reject_reason"] == "insufficient_margin_or_buying_power"


def test_duplicate_symbol_blocked():
    snap = LiveCapitalSnapshot.mock(stock_wallet=1_500_000, margin_wallet=1_500_000)
    snap.positions = [{"Symbol": "7203", "LeavesQty": 100}]
    row = evaluate_entry_capital(snap, symbol="7203.T", entry_price=2768.0, cap_limit=5)
    assert row["reject_reason"] == "duplicate_symbol"


def test_pending_orders_consume_cap():
    positions = [{"Symbol": "9984", "LeavesQty": 100}]
    orders = [{"Symbol": "7203", "Side": "2", "CashMargin": 2, "State": "1"}]
    used, open_n, pending = count_cap_slots(positions, orders)
    assert used == 2
    assert open_n == 1
    assert pending == 1
    assert "7203" in open_symbols(positions, orders)


def test_mock_equity_cap_matrix():
    price = 2768.0
    snap_300k = LiveCapitalSnapshot.mock(stock_wallet=300_000, margin_wallet=300_000)
    assert operational_cap_ok(snap_300k, entry_price=price, cap_limit=2)
    assert not operational_cap_ok(snap_300k, entry_price=price, cap_limit=5)

    snap_1m = LiveCapitalSnapshot.mock(stock_wallet=1_000_000, margin_wallet=1_000_000)
    assert operational_cap_ok(snap_1m, entry_price=price, cap_limit=5)


def test_min_equity_for_cap():
    cap2 = min_equity_for_cap(cap=2, entry_price=2768.0)
    assert cap2["required_margin_per_slot"] == 138400.0
    assert cap2["total_required_margin"] == 276800.0

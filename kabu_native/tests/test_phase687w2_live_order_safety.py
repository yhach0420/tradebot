"""Phase687W2 — Live Order Safety State Machine dry-run tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from small_paper.live_order_safety_sm import (
    KabuBrokerAdapter,
    LiveOrderSafetyEngine,
    MockBrokerAdapter,
    OrderLifecycleState,
    build_engine,
    can_transition,
    dryrun_position_sizing,
    lot_round_down,
    make_idempotency_key,
)


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        live_trading_enabled=False,
        order_enabled=False,
        dry_run=True,
        max_concurrent_positions=3,
    )


def _eng(td: Path, broker=None) -> LiveOrderSafetyEngine:
    return build_engine(
        output_dir=td / "orders",
        session_id="test/w2",
        broker=broker or MockBrokerAdapter(),
        config=_cfg(),
    )


def test_illegal_transition_rejected():
    assert not can_transition(OrderLifecycleState.FILLED, OrderLifecycleState.SUBMITTED)
    assert not can_transition(OrderLifecycleState.CANCELED, OrderLifecycleState.FILLED)


def test_idempotency_key_stable():
    a = make_idempotency_key(
        session_id="s", position_id="p", symbol="X", side="BUY", intent_sequence=1
    )
    b = make_idempotency_key(
        session_id="s", position_id="p", symbol="X", side="BUY", intent_sequence=1
    )
    assert a == b
    exit_k = make_idempotency_key(
        session_id="s",
        position_id="p",
        symbol="X",
        side="EXIT",
        intent_sequence=1,
        exit_reason="stop_hit",
    )
    assert exit_k != a


def test_lot_round_and_sizing():
    assert lot_round_down(250) == 200
    assert lot_round_down(99) == 0
    s = dryrun_position_sizing(
        equity=1_000_000,
        available_buying_power=2_000_000,
        current_gross_exposure=0,
        current_symbol_exposure=0,
        price=1000.0,
    )
    assert s["lot_rounded_quantity"] % 100 == 0
    assert s["baseline_quantity"] == 100


def test_full_fill_and_exit():
    with tempfile.TemporaryDirectory() as tmp:
        eng = _eng(Path(tmp))
        o = eng.handle_entry_signal(symbol="A", price=1000.0, position_id="p1")
        assert o.state == OrderLifecycleState.FILLED
        assert eng.ledger.active_reservation_count() == 0
        x = eng.handle_exit_signal(symbol="A", exit_reason="trailing_mfe_exit", position_id="p1")
        assert x.state == OrderLifecycleState.FILLED
        assert eng.ledger.open_positions.get("A", 0) == 0


def test_duplicate_entry_no_second_order():
    with tempfile.TemporaryDirectory() as tmp:
        eng = _eng(Path(tmp))
        a = eng.handle_entry_signal(symbol="B", price=1000.0, position_id="same")
        b = eng.handle_entry_signal(symbol="B", price=1000.0, position_id="same")
        assert a.order_id == b.order_id
        assert eng.duplicate_order_count == 1


def test_partial_then_cancel_then_exit():
    with tempfile.TemporaryDirectory() as tmp:
        eng = _eng(Path(tmp), MockBrokerAdapter(behavior="partial"))
        o = eng.handle_entry_signal(symbol="C", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.PARTIALLY_FILLED
        eng.cancel(o.order_id)
        assert eng.orders[o.order_id].state == OrderLifecycleState.CANCELED
        assert eng.ledger.active_reservation_count() == 0
        x = eng.handle_exit_signal(symbol="C", exit_reason="stop_hit", position_id="p")
        assert x.state == OrderLifecycleState.FILLED
        assert x.quantity == o.filled_qty


def test_timeout_after_reconcile_no_resubmit():
    with tempfile.TemporaryDirectory() as tmp:
        broker = MockBrokerAdapter(behavior="timeout_after")
        eng = _eng(Path(tmp), broker)
        o = eng.handle_entry_signal(symbol="D", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.UNKNOWN
        assert o.broker_order_id
        submits = broker.submit_count
        eng.reconcile_unknown(o.order_id)
        assert broker.submit_count == submits
        assert eng.orders[o.order_id].state == OrderLifecycleState.ACKNOWLEDGED


def test_startup_broker_only_blocks_entry():
    with tempfile.TemporaryDirectory() as tmp:
        broker = MockBrokerAdapter()
        broker.account.positions["E"] = 100
        eng = _eng(Path(tmp), broker)
        recon = eng.startup_reconciliation(local_positions={}, local_pending={})
        assert recon["recovery_required"]
        o = eng.handle_entry_signal(symbol="E", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.PRECHECK_REJECTED


def test_kill_switch_and_kabu_hard_fail():
    with tempfile.TemporaryDirectory() as tmp:
        eng = _eng(Path(tmp))
        eng.activate_kill_switch("manual")
        o = eng.handle_entry_signal(symbol="F", price=1000.0, position_id="p")
        assert o.state == OrderLifecycleState.PRECHECK_REJECTED
    with pytest.raises(RuntimeError, match="HARD_FAIL"):
        KabuBrokerAdapter().submit_entry_order({"symbol": "X"})


def test_flags_remain_disabled():
    cfg = _cfg()
    assert cfg.live_trading_enabled is False
    assert cfg.order_enabled is False

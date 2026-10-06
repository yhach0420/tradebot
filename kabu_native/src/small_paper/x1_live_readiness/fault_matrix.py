"""Fault matrix for the mock-broker execution book. No real orders."""

from __future__ import annotations

from small_paper.am_pm_session_policy import AmPmSessionPolicy
from small_paper.x1_live_readiness.mock_broker import MockBroker
from small_paper.x1_live_readiness.state_machine import (
    BrokerView,
    Journal,
    LiveExecutionBook,
    OperatingMode,
    PositionState,
)

AID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V12"
SYM = "7203.T"


def _book(broker, journal=None, on_discord=None) -> LiveExecutionBook:
    return LiveExecutionBook(activation_id=AID, broker=broker, journal=journal, on_discord=on_discord)


def _entry(book: LiveExecutionBook, *, trade: str = "t1", signal: str = "s1", symbol: str = SYM, qty: int = 100, ts: str = "t0"):
    return book.request_entry(
        strategy_trade_id=trade,
        signal_uid=signal,
        symbol=symbol,
        intended_qty=qty,
        created_at=ts,
    )


def _exit(book: LiveExecutionBook, *, trade: str = "t1", signal: str = "x1", symbol: str = SYM, qty: int = 100, ts: str = "t2"):
    return book.request_exit(
        strategy_trade_id=trade,
        signal_uid=signal,
        symbol=symbol,
        intended_qty=qty,
        created_at=ts,
    )


def _intent(book: LiveExecutionBook, symbol: str = SYM):
    pos = book.positions[symbol]
    iid = pos.get("exit_intent_id") or pos.get("entry_intent_id")
    return book.intents[iid]


def _buy_oid(broker: MockBroker) -> str:
    return next(oid for oid, order in broker.orders.items() if order["side"] == "BUY")


def _restore(book: LiveExecutionBook, broker: MockBroker) -> LiveExecutionBook:
    nxt = _book(broker, Journal())
    nxt.replay_journal(list(book.journal.rows))
    nxt.reconcile(broker.view(), "restart", reason="PROCESS_RESTART")
    return nxt


def _fp(book: LiveExecutionBook, broker: MockBroker) -> tuple:
    return (
        book.mode.value,
        book.durability_ok,
        tuple(sorted((k, v["state"], int(v["confirmed_qty"])) for k, v in book.positions.items())),
        broker.submit_calls,
        broker.cancel_calls,
        tuple(sorted(broker.orders)),
    )


def _row(name: str, book: LiveExecutionBook, broker: MockBroker, *, group: str, expected_qty: int, expected_submits: int, entry_blocked: bool, parity: bool, symbol: str = SYM) -> dict:
    blocked = bool(book.entry_block_reason(symbol))
    actual_parity = _parity(book, broker)
    ok = (
        book.confirmed_qty(symbol) == expected_qty
        and broker.submit_calls == expected_submits
        and blocked is entry_blocked
        and actual_parity is parity
        and book.duplicate_position_attempts >= 0
    )
    return {
        "scenario": name,
        "group": group,
        "passed": ok,
        "confirmed_qty": book.confirmed_qty(symbol),
        "expected_qty": expected_qty,
        "submit_calls": broker.submit_calls,
        "expected_submits": expected_submits,
        "entry_blocked": blocked,
        "parity": actual_parity,
        "state": book.positions.get(symbol, {}).get("state", "NO_ORDER"),
        "mode": book.mode.value,
        "duplicate_position_attempts": book.duplicate_position_attempts,
        "duplicate_order_attempts": book.duplicate_order_attempts,
        "fingerprint": _fp(book, broker),
    }


def _parity(book: LiveExecutionBook, broker: MockBroker) -> bool:
    if broker.unreadable:
        return False
    try:
        view = broker.view()
    except Exception:
        return False
    return bool(book._parity(view))


def _fill_entry(book: LiveExecutionBook, broker: MockBroker, qty: int, exec_id: str, ts: str = "t1") -> str:
    oid = _buy_oid(broker)
    broker.fill(oid, exec_id, qty)
    intent = _intent(book)
    book.on_fill(intent.order_intent_id, exec_id, qty, ts, broker_order_id=oid)
    return oid


def scenario_submit_timeout() -> dict:
    broker = MockBroker(timeout=True)
    book = _book(broker)
    first = _entry(book)
    _entry(book, signal="s2", trade="t2")
    book.retry(first["order_intent_id"], "t1")
    book.on_ack_timeout(first["order_intent_id"], "t1")
    return _row("submit timeout", book, broker, group="order", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)


def scenario_ack_lost() -> dict:
    broker = MockBroker(ack_lost=True)
    book = _book(broker)
    first = _entry(book)
    book.on_ack_timeout(first["order_intent_id"], "t1")
    book.retry(first["order_intent_id"], "t2")
    return _row("ACK lost", book, broker, group="order", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)


def scenario_reject() -> dict:
    broker = MockBroker(reject=True)
    book = _book(broker)
    _entry(book)
    assert book.positions[SYM]["state"] == "NO_ORDER"
    broker.reject = False
    again = _entry(book, signal="s2", trade="t2")
    row = _row(
        "reject",
        book,
        broker,
        group="order",
        expected_qty=0,
        expected_submits=2,
        entry_blocked=True,
        parity=True,
    )
    row["passed"] = row["passed"] and again["ok"] is True and book.confirmed_qty(SYM) == 0
    return row


def scenario_partial_30() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 30, "e1")
    blocked = _entry(book, signal="s2", trade="t2")
    row = _row("partial fill 30/100", book, broker, group="partial", expected_qty=30, expected_submits=1, entry_blocked=True, parity=True)
    row["second_entry_ok"] = blocked["ok"]
    row["passed"] = row["passed"] and blocked["ok"] is False and book.positions[SYM]["state"] == PositionState.PARTIALLY_FILLED.value
    return row


def scenario_partial_then_full() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 30, "e1")
    _fill_entry(book, broker, 70, "e2")
    row = _row("partial fill 30→100", book, broker, group="partial", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and book.positions[SYM]["state"] == PositionState.FILLED.value
    return row


def scenario_fill_after_cancel() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    intent = _intent(book)
    book.request_cancel(intent.order_intent_id, "t1")
    _fill_entry(book, broker, 100, "e1", "t3")
    row = _row("fill after cancel request", book, broker, group="order", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and broker.cancel_calls == 1
    return row


def scenario_duplicate_fill() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    oid = _fill_entry(book, broker, 100, "e1")
    broker.fill(oid, "e1", 100)
    intent = _intent(book)
    book.on_fill(intent.order_intent_id, "e1", 100, "t9", broker_order_id=oid)
    return _row("duplicate fill callback", book, broker, group="partial", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)


def scenario_out_of_order() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    oid = _buy_oid(broker)
    intent = _intent(book)
    broker.fill(oid, "e1", 100)
    book.on_fill(intent.order_intent_id, "e1", 100, "t1", broker_order_id=oid)
    book.on_ack(intent.order_intent_id, oid, "t2")
    row = _row("out-of-order callback", book, broker, group="order", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and book.positions[SYM]["state"] == PositionState.FILLED.value
    return row


def scenario_disconnect(where: str) -> dict:
    broker = MockBroker(ack_lost=(where == "before ACK"))
    book = _book(broker)
    _entry(book)
    if where == "before ACK":
        book.on_ack_timeout(_intent(book).order_intent_id, "t1")
    if where in {"after ACK", "during partial fill", "during EXIT"}:
        pass
    if where == "during partial fill":
        _fill_entry(book, broker, 30, "e1")
    if where == "during EXIT":
        _fill_entry(book, broker, 100, "e1")
        _exit(book)
    before = broker.submit_calls
    book.note_disconnect(where, "t5")
    book.reconcile(broker.view(), "t6", reason=f"DISCONNECT_{where}")
    _entry(book, signal="s9", trade="t9")
    expected = {"before ACK": 0, "after ACK": 0, "during partial fill": 30, "during EXIT": 100}[where]
    submits = 2 if where == "during EXIT" else 1
    row = _row(f"disconnect {where}", book, broker, group="disconnect", expected_qty=expected, expected_submits=submits, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and broker.submit_calls == before
    return row


def scenario_token_expiry() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    book.note_token_expiry("t1")
    _entry(book, signal="s2", trade="t2")
    return _row("token expiry", book, broker, group="disconnect", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)


def scenario_station_loss() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    book.note_station_loss("t1")
    blocked = _entry(book, signal="s2", trade="t2")
    row = _row("Kabu Station loss", book, broker, group="disconnect", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and blocked["reason"] == OperatingMode.RECONCILIATION_REQUIRED.value
    return row


def scenario_market_bus_loss() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    book.note_market_bus(stale=True, ts="t3")
    _entry(book, signal="s2", trade="t2", symbol="9984.T")
    row = _row("MarketBus loss", book, broker, group="disconnect", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and "9984.T" not in book.positions
    return row


def scenario_process_restart() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    nxt = _restore(book, broker)
    _entry(nxt, signal="s2", trade="t2")
    row = _row("process restart", nxt, broker, group="crash", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and nxt.positions[SYM]["state"] == PositionState.FILLED.value
    return row


def scenario_local_zero_broker_100() -> dict:
    broker = MockBroker()
    broker.force_position(SYM, 100)
    book = _book(broker)
    book.reconcile(broker.view(), "t0", reason="LOCAL_0_BROKER_100")
    _entry(book)
    return _row("local 0 / broker 100", book, broker, group="reconcile", expected_qty=100, expected_submits=0, entry_blocked=True, parity=True)


def scenario_local_100_broker_50() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    broker.force_position(SYM, 50)
    book.reconcile(broker.view(), "t3", reason="LOCAL_100_BROKER_50")
    _entry(book, signal="s2", trade="t2")
    return _row("local 100 / broker 50", book, broker, group="reconcile", expected_qty=50, expected_submits=1, entry_blocked=True, parity=True)


def scenario_broker_active_only() -> dict:
    broker = MockBroker()
    broker.orders["B-EXT"] = {
        "order_id": "B-EXT",
        "order_intent_id": "",
        "strategy_trade_id": "ext",
        "signal_uid": "ext",
        "side": "BUY",
        "symbol": SYM,
        "intended_qty": 100,
        "cum_qty": 0,
        "status": "WORKING",
        "execs": [],
    }
    book = _book(broker)
    book.reconcile(broker.view(), "t0", reason="BROKER_ACTIVE_ONLY")
    _entry(book)
    row = _row("broker active order only", book, broker, group="reconcile", expected_qty=0, expected_submits=0, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and book.positions[SYM]["state"] == PositionState.ACKNOWLEDGED.value
    return row


def scenario_local_pending_only() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    broker.orders.clear()
    book.reconcile(broker.view(), "t2", reason="LOCAL_PENDING_ONLY")
    _entry(book, signal="s2", trade="t2")
    return _row("local pending only", book, broker, group="reconcile", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)


def scenario_unreadable_is_not_zero() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    broker.unreadable = True
    result = book.reconcile(BrokerView(readable=False, reason="EMPTY_PAYLOAD"), "t4")
    _entry(book, signal="s2", trade="t2")
    row = _row("unreadable broker is not zero", book, broker, group="reconcile", expected_qty=100, expected_submits=1, entry_blocked=True, parity=False)
    row["passed"] = row["passed"] and result["reason"] == "BROKER_UNREADABLE" and book.confirmed_qty(SYM) == 100
    return row


def scenario_session(hhmm: str, kind: str) -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    if kind == "pending ENTRY":
        expected = 0
        submits = 1
    elif kind == "partial ENTRY":
        _fill_entry(book, broker, 30, "e1")
        expected = 30
        submits = 1
    elif kind == "pending EXIT":
        _fill_entry(book, broker, 100, "e1")
        _exit(book)
        expected = 100
        submits = 2
    elif kind == "partial EXIT":
        _fill_entry(book, broker, 100, "e1")
        _exit(book)
        sell = next(oid for oid, order in broker.orders.items() if order["side"] == "SELL")
        broker.fill(sell, "x1", 40)
        book.on_fill(_intent(book).order_intent_id, "x1", 40, "t3", broker_order_id=sell)
        expected = 60
        submits = 2
    else:
        raise AssertionError(kind)
    before = broker.submit_calls
    boundary = book.session_boundary(hhmm, f"boundary-{hhmm}")
    _entry(book, signal="s9", trade="t9")
    row = _row(f"session close {hhmm} {kind}", book, broker, group="session_close", expected_qty=expected, expected_submits=submits, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and boundary["submitted"] == 0 and broker.submit_calls == before
    row["hhmm"] = hhmm
    return row


def scenario_crash(kind: str) -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    if kind == "position open":
        _fill_entry(book, broker, 100, "e1")
        expected = 100
        submits = 1
    elif kind == "partial fill":
        _fill_entry(book, broker, 30, "e1")
        expected = 30
        submits = 1
    elif kind == "EXIT pending":
        _fill_entry(book, broker, 100, "e1")
        _exit(book)
        expected = 100
        submits = 2
    else:
        raise AssertionError(kind)
    nxt = _restore(book, broker)
    if kind == "EXIT pending":
        _exit(nxt, signal="x2", trade="t1")
    _entry(nxt, signal="s9", trade="t9")
    row = _row(f"crash {kind}", nxt, broker, group="crash", expected_qty=expected, expected_submits=submits, entry_blocked=True, parity=True)
    owned = nxt.positions[SYM].get("exit_intent_id") or nxt.positions[SYM].get("entry_intent_id")
    row["passed"] = row["passed"] and bool(nxt.intents[owned].broker_order_id)
    return row


def scenario_disk_failure() -> dict:
    broker = MockBroker()
    book = _book(broker, Journal(fail=True))
    denied = _entry(book)
    return {
        "scenario": "disk write failure",
        "group": "durability",
        "passed": denied["reason"] == "LEDGER_DURABILITY_FAILURE" and broker.submit_calls == 0 and SYM not in book.positions and book.entry_block_reason(SYM) == "LEDGER_DURABILITY_FAILURE",
        "confirmed_qty": 0,
        "expected_qty": 0,
        "submit_calls": broker.submit_calls,
        "expected_submits": 0,
        "entry_blocked": True,
        "parity": True,
        "state": "NO_ORDER",
        "mode": book.mode.value,
        "duplicate_position_attempts": 0,
        "duplicate_order_attempts": 0,
        "fingerprint": (book.mode.value, broker.submit_calls, SYM not in book.positions),
    }


def scenario_disk_full_after_open() -> dict:
    broker = MockBroker()
    journal = Journal()
    book = _book(broker, journal)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    journal.fail = True
    before = book.confirmed_qty(SYM)
    denied = _entry(book, symbol="9984.T", signal="s2", trade="t2")
    return {
        "scenario": "disk full simulation",
        "group": "durability",
        "passed": denied["reason"] == "LEDGER_DURABILITY_FAILURE" and book.confirmed_qty(SYM) == before == 100 and "9984.T" not in book.positions and broker.submit_calls == 1,
        "confirmed_qty": book.confirmed_qty(SYM),
        "expected_qty": 100,
        "submit_calls": 1,
        "expected_submits": 1,
        "entry_blocked": True,
        "parity": True,
        "state": book.positions[SYM]["state"],
        "mode": book.mode.value,
        "duplicate_position_attempts": book.duplicate_position_attempts,
        "duplicate_order_attempts": book.duplicate_order_attempts,
        "fingerprint": (book.confirmed_qty(SYM), broker.submit_calls, book.mode.value),
    }


def scenario_clock_skew() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    book.set_causal_now(100.0)
    accepted = book.observe_clock(100.0, source="board")
    rejected = book.observe_clock(101.0, source="future_board")
    row = _row("clock skew", book, broker, group="durability", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and accepted and not rejected and book.clock_rejects == 1
    return row


def scenario_heartbeat_stall() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    book.note_heartbeat_stall("t3")
    _entry(book, symbol="9984.T", signal="s2", trade="t2")
    row = _row("heartbeat stall", book, broker, group="durability", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and book.mode == OperatingMode.ENTRY_DISABLED and "9984.T" not in book.positions
    return row


def scenario_discord_failure() -> dict:
    broker = MockBroker()

    def _boom(_payload):
        raise RuntimeError("discord down")

    book = _book(broker, on_discord=_boom)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    row = _row("Discord failure", book, broker, group="durability", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and book.discord_failures == 1 and book.positions[SYM]["state"] == PositionState.FILLED.value
    return row


def scenario_exit_only() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    book.set_mode(OperatingMode.EXIT_ONLY, "OPERATOR_EXIT_ONLY", "t3")
    denied = _entry(book, symbol="9984.T", signal="s2", trade="t2")
    allowed = _exit(book, qty=100)
    row = _row("mode EXIT_ONLY", book, broker, group="emergency", expected_qty=100, expected_submits=2, entry_blocked=True, parity=True)
    row["passed"] = row["passed"] and denied["ok"] is False and allowed["ok"] is True and "9984.T" not in book.positions
    return row


def scenario_halted() -> dict:
    broker = MockBroker()
    book = _book(broker)
    _entry(book)
    _fill_entry(book, broker, 100, "e1")
    book.set_mode(OperatingMode.HALTED, "OPERATOR_HALT", "t3")
    before = broker.submit_calls
    denied_entry = _entry(book, symbol="9984.T", signal="s2", trade="t2")
    denied_exit = _exit(book, qty=100)
    row = _row("mode HALTED", book, broker, group="emergency", expected_qty=100, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = (
        row["passed"]
        and denied_entry["ok"] is False
        and denied_exit["reason"] == "HALTED"
        and broker.submit_calls == before
        and any(item.get("event") == "MODE" and item.get("to") == "HALTED" for item in book.transitions)
    )
    return row


def scenario_idempotent_retry() -> dict:
    broker = MockBroker()
    book = _book(broker)
    first = _entry(book)
    second = _entry(book)
    third = book.retry(first["order_intent_id"], "t9")
    intent = book.intents[first["order_intent_id"]]
    row = _row("idempotent retry", book, broker, group="order", expected_qty=0, expected_submits=1, entry_blocked=True, parity=True)
    row["passed"] = (
        row["passed"]
        and second.get("reattached") is True
        and third["submit_count"] == 1
        and intent.created_at == "t0"
        and intent.strategy_trade_id == "t1"
        and intent.signal_uid == "s1"
        and intent.activation_id == AID
    )
    return row


def all_scenario_fns():
    fns = [
        scenario_submit_timeout,
        scenario_ack_lost,
        scenario_reject,
        scenario_partial_30,
        scenario_partial_then_full,
        scenario_fill_after_cancel,
        scenario_duplicate_fill,
        scenario_out_of_order,
        lambda: scenario_disconnect("before ACK"),
        lambda: scenario_disconnect("after ACK"),
        lambda: scenario_disconnect("during partial fill"),
        lambda: scenario_disconnect("during EXIT"),
        scenario_token_expiry,
        scenario_station_loss,
        scenario_market_bus_loss,
        scenario_process_restart,
        scenario_local_zero_broker_100,
        scenario_local_100_broker_50,
        scenario_broker_active_only,
        scenario_local_pending_only,
        scenario_unreadable_is_not_zero,
        scenario_idempotent_retry,
        scenario_disk_failure,
        scenario_disk_full_after_open,
        scenario_clock_skew,
        scenario_heartbeat_stall,
        scenario_discord_failure,
        scenario_exit_only,
        scenario_halted,
        scenario_crash_position := lambda: scenario_crash("position open"),
        lambda: scenario_crash("partial fill"),
        lambda: scenario_crash("EXIT pending"),
    ]
    del scenario_crash_position
    am = AmPmSessionPolicy.from_kind("am").force_close
    pm = AmPmSessionPolicy.from_kind("pm").force_close
    for hhmm in (am, pm):
        for kind in ("pending ENTRY", "partial ENTRY", "pending EXIT", "partial EXIT"):
            fns.append(lambda hhmm=hhmm, kind=kind: scenario_session(hhmm, kind))
    return fns


def run_all() -> list[dict]:
    rows = []
    for fn in all_scenario_fns():
        first = fn()
        second = fn()
        first["deterministic"] = first["fingerprint"] == second["fingerprint"]
        first["passed"] = bool(first["passed"] and first["deterministic"])
        first.pop("fingerprint", None)
        rows.append(first)
    return rows

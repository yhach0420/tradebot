"""Position-level live execution state machine.

Broker calls go only to an injected port. This module never imports a broker client.
Confirmed fill quantity is the only position quantity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class PositionState(str, Enum):
    NO_ORDER = "NO_ORDER"
    ENTRY_INTENT = "ENTRY_INTENT"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    EXIT_INTENT = "EXIT_INTENT"
    EXIT_SUBMITTING = "EXIT_SUBMITTING"
    EXIT_ACKNOWLEDGED = "EXIT_ACKNOWLEDGED"
    PARTIALLY_EXITED = "PARTIALLY_EXITED"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class OperatingMode(str, Enum):
    NORMAL = "NORMAL"
    ENTRY_DISABLED = "ENTRY_DISABLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    EXIT_ONLY = "EXIT_ONLY"
    HALTED = "HALTED"


UNRESOLVED_ENTRY = frozenset(
    {
        PositionState.ENTRY_INTENT,
        PositionState.SUBMITTING,
        PositionState.ACKNOWLEDGED,
        PositionState.PARTIALLY_FILLED,
        PositionState.UNKNOWN,
        PositionState.RECONCILIATION_REQUIRED,
    }
)
UNRESOLVED_EXIT = frozenset(
    {
        PositionState.EXIT_INTENT,
        PositionState.EXIT_SUBMITTING,
        PositionState.EXIT_ACKNOWLEDGED,
        PositionState.PARTIALLY_EXITED,
    }
)
ENTRY_SIDES = frozenset({"BUY"})
EXIT_SIDES = frozenset({"SELL"})


class BrokerTimeout(Exception):
    pass


class BrokerUnreadable(Exception):
    pass


@dataclass
class OrderIntent:
    strategy_trade_id: str
    signal_uid: str
    order_intent_id: str
    side: str
    symbol: str
    intended_qty: int
    created_at: str
    activation_id: str
    broker_order_id: str = ""
    submit_count: int = 0
    confirmed_qty: int = 0
    exec_ids: set[str] = field(default_factory=set)
    terminal: str = ""


@dataclass
class BrokerView:
    readable: bool
    positions: dict[str, int] = field(default_factory=dict)
    orders: list[dict] = field(default_factory=list)
    reason: str = ""


def make_order_intent_id(
    *,
    activation_id: str,
    strategy_trade_id: str,
    signal_uid: str,
    side: str,
    symbol: str,
    intended_qty: int,
) -> str:
    raw = "|".join(
        [
            activation_id,
            strategy_trade_id,
            signal_uid,
            side,
            symbol,
            str(int(intended_qty)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Journal:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict] = []
        self.fail = fail
        self.write_failures = 0

    def append(self, row: dict) -> bool:
        if self.fail:
            self.write_failures += 1
            return False
        self.rows.append(dict(row))
        return True


class LiveExecutionBook:
    """Local book. Intended quantity is never copied onto the position."""

    def __init__(
        self,
        *,
        activation_id: str,
        broker,
        journal: Optional[Journal] = None,
        on_discord: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.activation_id = activation_id
        self.broker = broker
        self.journal = journal or Journal()
        self.on_discord = on_discord
        self.mode = OperatingMode.NORMAL
        self.mode_reason = ""
        self.durability_ok = True
        self.positions: dict[str, dict] = {}
        self.intents: dict[str, OrderIntent] = {}
        self.transitions: list[dict] = []
        self.duplicate_position_attempts = 0
        self.duplicate_order_attempts = 0
        self.discord_failures = 0
        self.clock_rejects = 0
        self.causal_now = 0.0

    def set_causal_now(self, ts: float) -> None:
        self.causal_now = float(ts)

    def observe_clock(self, event_ts: float, *, source: str) -> bool:
        if float(event_ts) > self.causal_now:
            self.clock_rejects += 1
            self._log("CLOCK_SKEW_REJECT", reason=source, ts=self.causal_now)
            return False
        return True

    def set_mode(self, mode: OperatingMode, reason: str, ts: str) -> None:
        previous = self.mode
        self.mode = mode
        self.mode_reason = reason
        self._log("MODE", reason=reason, ts=ts, extra={"from": previous.value, "to": mode.value})

    def entry_block_reason(self, symbol: str) -> str:
        if not self.durability_ok:
            return "LEDGER_DURABILITY_FAILURE"
        if self.mode != OperatingMode.NORMAL:
            return self.mode.value
        for pos in self.positions.values():
            state = PositionState(pos["state"])
            if state in {PositionState.UNKNOWN, PositionState.RECONCILIATION_REQUIRED}:
                return "STATE_UNCERTAIN"
        pos = self.positions.get(symbol)
        if pos is None:
            return ""
        state = PositionState(pos["state"])
        if state in UNRESOLVED_ENTRY or state in UNRESOLVED_EXIT:
            return "UNRESOLVED_ORDER"
        if state in {PositionState.FILLED, PositionState.PARTIALLY_FILLED}:
            return "POSITION_OPEN"
        return ""

    def request_entry(
        self,
        *,
        strategy_trade_id: str,
        signal_uid: str,
        symbol: str,
        intended_qty: int,
        created_at: str,
    ) -> dict:
        intent = self._build_intent(
            strategy_trade_id=strategy_trade_id,
            signal_uid=signal_uid,
            side="BUY",
            symbol=symbol,
            intended_qty=int(intended_qty),
            created_at=created_at,
        )
        existing = self.intents.get(intent.order_intent_id)
        if existing is not None:
            self.duplicate_order_attempts += 1
            return {
                "ok": True,
                "reattached": True,
                "order_intent_id": existing.order_intent_id,
                "submit_count": existing.submit_count,
            }
        block = self.entry_block_reason(symbol)
        if block:
            if block in {"POSITION_OPEN", "UNRESOLVED_ORDER"}:
                self.duplicate_position_attempts += 1
            self._log("ENTRY_BLOCKED", symbol=symbol, reason=block, ts=created_at)
            return {"ok": False, "reason": block}
        row = _intent_row(intent, "ENTRY_INTENT")
        if not self.journal.append(row):
            return self._durability_fail("ENTRY_INTENT", created_at)
        self.intents[intent.order_intent_id] = intent
        self.positions[symbol] = _blank_position(symbol, strategy_trade_id, PositionState.ENTRY_INTENT, intent.order_intent_id)
        self._transition(symbol, PositionState.NO_ORDER, PositionState.ENTRY_INTENT, "ENTRY_INTENT", created_at)
        return self._submit_existing(intent, PositionState.SUBMITTING, created_at)

    def request_exit(
        self,
        *,
        strategy_trade_id: str,
        signal_uid: str,
        symbol: str,
        intended_qty: int,
        created_at: str,
    ) -> dict:
        if self.mode == OperatingMode.HALTED:
            self._log("EXIT_BLOCKED", symbol=symbol, reason="HALTED", ts=created_at)
            return {"ok": False, "reason": "HALTED"}
        pos = self.positions.get(symbol)
        if pos is None:
            return {"ok": False, "reason": "NO_POSITION"}
        state = PositionState(pos["state"])
        if state in UNRESOLVED_EXIT or state in {PositionState.UNKNOWN, PositionState.RECONCILIATION_REQUIRED}:
            self.duplicate_order_attempts += 1
            self._log("EXIT_BLOCKED", symbol=symbol, reason="UNRESOLVED_EXIT", ts=created_at)
            return {"ok": False, "reason": "UNRESOLVED_EXIT"}
        confirmed = int(pos["confirmed_qty"])
        if confirmed <= 0:
            return {"ok": False, "reason": "NO_CONFIRMED_QTY"}
        if int(intended_qty) != confirmed:
            self._log("EXIT_BLOCKED", symbol=symbol, reason="QTY_EXCEEDS_OR_DIFFERS_FROM_CONFIRMED", ts=created_at)
            return {"ok": False, "reason": "CONFIRMED_QTY_REQUIRED", "confirmed_qty": confirmed}
        intent = self._build_intent(
            strategy_trade_id=strategy_trade_id,
            signal_uid=signal_uid,
            side="SELL",
            symbol=symbol,
            intended_qty=confirmed,
            created_at=created_at,
        )
        if intent.order_intent_id in self.intents and self.intents[intent.order_intent_id].submit_count:
            self.duplicate_order_attempts += 1
            found = self.intents[intent.order_intent_id]
            return {"ok": True, "reattached": True, "order_intent_id": found.order_intent_id, "submit_count": found.submit_count}
        if not self.journal.append(_intent_row(intent, "EXIT_INTENT")):
            return self._durability_fail("EXIT_INTENT", created_at)
        self.intents[intent.order_intent_id] = intent
        pos["exit_intent_id"] = intent.order_intent_id
        pos["state"] = PositionState.EXIT_INTENT.value
        self._transition(symbol, state, PositionState.EXIT_INTENT, "EXIT_INTENT", created_at)
        return self._submit_existing(intent, PositionState.EXIT_SUBMITTING, created_at)

    def retry(self, order_intent_id: str, ts: str) -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        self.duplicate_order_attempts += 1
        self._log("RETRY_NO_SUBMIT", symbol=intent.symbol, reason=order_intent_id, ts=ts)
        return {
            "ok": True,
            "submit_count": intent.submit_count,
            "broker_order_id": intent.broker_order_id,
            "created_at": intent.created_at,
        }

    def on_ack_timeout(self, order_intent_id: str, ts: str) -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        self._set_symbol_state(intent.symbol, PositionState.UNKNOWN, "ACK_TIMEOUT", ts)
        view = self._safe_broker_view()
        return self.reconcile(view, ts, reason="ACK_TIMEOUT")

    def on_ack(self, order_intent_id: str, broker_order_id: str, ts: str) -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        if intent.broker_order_id and intent.broker_order_id != broker_order_id:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "BROKER_ORDER_ID_MISMATCH", ts)
            self._set_symbol_state(intent.symbol, PositionState.RECONCILIATION_REQUIRED, "BROKER_ORDER_ID_MISMATCH", ts)
            return {"ok": False, "reason": "BROKER_ORDER_ID_MISMATCH"}
        intent.broker_order_id = broker_order_id
        if not self.journal.append({"event": "ACK", "order_intent_id": order_intent_id, "broker_order_id": broker_order_id, "ts": ts}):
            return self._durability_fail("ACK", ts)
        nxt = _ack_state(intent)
        self._set_symbol_state(intent.symbol, nxt, "ACK", ts)
        return {"ok": True, "state": nxt.value}

    def on_reject(self, order_intent_id: str, ts: str) -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        if intent.terminal == "REJECTED":
            return {"ok": True, "duplicate": True, "confirmed_qty": self.confirmed_qty(intent.symbol)}
        if not self.journal.append({"event": "REJECT", "order_intent_id": order_intent_id, "ts": ts}):
            return self._durability_fail("REJECT", ts)
        intent.terminal = "REJECTED"
        pos = self.positions.get(intent.symbol)
        if intent.side == "BUY":
            self._set_symbol_state(intent.symbol, PositionState.NO_ORDER, "REJECTED", ts)
            if pos is not None:
                pos["confirmed_qty"] = 0
        else:
            self._set_symbol_state(intent.symbol, PositionState.FILLED, "EXIT_REJECTED", ts)
        return {"ok": True, "confirmed_qty": self.confirmed_qty(intent.symbol)}

    def on_fill(self, order_intent_id: str, exec_id: str, qty: int, ts: str, *, broker_order_id: str = "") -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "FILL_FOR_UNKNOWN_INTENT", ts)
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        if exec_id in intent.exec_ids:
            return {"ok": True, "duplicate": True, "confirmed_qty": self.confirmed_qty(intent.symbol)}
        if broker_order_id:
            if intent.broker_order_id and intent.broker_order_id != broker_order_id:
                self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "FILL_ORDER_MISMATCH", ts)
                return {"ok": False, "reason": "FILL_ORDER_MISMATCH"}
            intent.broker_order_id = broker_order_id
        delta = int(qty)
        if delta <= 0:
            return {"ok": False, "reason": "NON_POSITIVE_FILL"}
        if intent.confirmed_qty + delta > intent.intended_qty:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "FILL_EXCEEDS_INTENDED", ts)
            self._set_symbol_state(intent.symbol, PositionState.RECONCILIATION_REQUIRED, "FILL_EXCEEDS_INTENDED", ts)
            return {"ok": False, "reason": "FILL_EXCEEDS_INTENDED"}
        row = {
            "event": "FILL",
            "order_intent_id": order_intent_id,
            "exec_id": exec_id,
            "qty": delta,
            "broker_order_id": intent.broker_order_id,
            "ts": ts,
        }
        if not self.journal.append(row):
            return self._durability_fail("FILL", ts)
        intent.exec_ids.add(exec_id)
        intent.confirmed_qty += delta
        pos = self.positions[intent.symbol]
        if intent.side == "BUY":
            pos["entry_confirmed_qty"] = intent.confirmed_qty
            pos["confirmed_qty"] = intent.confirmed_qty - int(pos.get("exit_confirmed_qty") or 0)
            nxt = PositionState.FILLED if intent.confirmed_qty == intent.intended_qty else PositionState.PARTIALLY_FILLED
        else:
            pos["exit_confirmed_qty"] = intent.confirmed_qty
            pos["confirmed_qty"] = int(pos["entry_confirmed_qty"]) - intent.confirmed_qty
            nxt = PositionState.FLAT if pos["confirmed_qty"] == 0 else PositionState.PARTIALLY_EXITED
        self._set_symbol_state(intent.symbol, nxt, "FILL", ts)
        self._notify({"kind": "FILL", "symbol": intent.symbol, "confirmed_qty": pos["confirmed_qty"]})
        return {"ok": True, "confirmed_qty": int(pos["confirmed_qty"]), "state": nxt.value}

    def request_cancel(self, order_intent_id: str, ts: str) -> dict:
        intent = self.intents.get(order_intent_id)
        if intent is None:
            return {"ok": False, "reason": "UNKNOWN_INTENT"}
        if not self.journal.append({"event": "CANCEL_REQUEST", "order_intent_id": order_intent_id, "ts": ts}):
            return self._durability_fail("CANCEL_REQUEST", ts)
        intent.terminal = intent.terminal or "CANCEL_REQUESTED"
        self.broker.request_cancel(intent)
        self._log("CANCEL_REQUEST", symbol=intent.symbol, reason=order_intent_id, ts=ts)
        return {"ok": True, "confirmed_qty": self.confirmed_qty(intent.symbol), "submit_count": intent.submit_count}

    def reconcile(self, view: BrokerView, ts: str, *, reason: str = "RECONCILE") -> dict:
        if not view.readable:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, view.reason or "BROKER_UNREADABLE", ts)
            for symbol in list(self.positions):
                if PositionState(self.positions[symbol]["state"]) != PositionState.FLAT:
                    self._set_symbol_state(symbol, PositionState.RECONCILIATION_REQUIRED, "BROKER_UNREADABLE", ts)
            self._log("RECONCILE_UNREADABLE", reason=view.reason or "BROKER_UNREADABLE", ts=ts)
            return {"ok": False, "reason": "BROKER_UNREADABLE", "parity": False, "entry_blocked": True}
        mismatch = False
        broker_orders = {str(item.get("order_id") or ""): item for item in view.orders}
        seen_order_ids = set()
        for item in view.orders:
            oid = str(item.get("order_id") or "")
            symbol = str(item["symbol"])
            intent = self._intent_for_broker(item)
            if intent is None:
                mismatch = True
                intent = self._reattach_broker_order(item, ts)
            elif not intent.broker_order_id:
                intent.broker_order_id = oid
                intent.submit_count = max(intent.submit_count, 1)
            seen_order_ids.add(intent.order_intent_id)
            cum = int(item.get("cum_qty") or 0)
            self._adopt_order_cum(intent, cum, ts)
            self._promote_working(intent, item, ts)
        for intent in list(self.intents.values()):
            if intent.terminal in {"REJECTED", "ABSENT"}:
                continue
            if intent.submit_count and intent.broker_order_id and intent.broker_order_id not in broker_orders:
                if intent.confirmed_qty < intent.intended_qty:
                    mismatch = True
                    intent.terminal = "ABSENT"
                    if intent.side == "BUY" and intent.confirmed_qty == 0:
                        self._set_symbol_state(intent.symbol, PositionState.NO_ORDER, "LOCAL_PENDING_ABSENT_AT_BROKER", ts)
                    self._log("LOCAL_ORDER_ABSENT", symbol=intent.symbol, reason=intent.broker_order_id, ts=ts)
        for symbol, broker_qty in view.positions.items():
            local = self.confirmed_qty(symbol)
            if int(broker_qty) != local:
                mismatch = True
                self._adopt_position_qty(symbol, int(broker_qty), ts)
        if mismatch:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, reason, ts)
        parity = self._parity(view)
        self._log("RECONCILE", reason=reason, ts=ts, extra={"parity": parity, "mismatch": mismatch})
        return {"ok": True, "parity": parity, "mismatch": mismatch, "entry_blocked": bool(self.entry_block_reason(next(iter(self.positions), "")))}

    def session_boundary(self, hhmm: str, ts: str) -> dict:
        """Record the boundary. Does not submit a liquidation order."""
        self.set_mode(OperatingMode.ENTRY_DISABLED, f"SESSION_BOUNDARY_{hhmm}", ts)
        rows = []
        for symbol, pos in self.positions.items():
            rows.append(
                {
                    "symbol": symbol,
                    "state": pos["state"],
                    "confirmed_qty": int(pos["confirmed_qty"]),
                    "entry_confirmed_qty": int(pos["entry_confirmed_qty"]),
                    "exit_confirmed_qty": int(pos["exit_confirmed_qty"]),
                    "hhmm": hhmm,
                }
            )
        self._log("SESSION_BOUNDARY", reason=hhmm, ts=ts, extra={"positions": rows})
        return {"hhmm": hhmm, "submitted": 0, "positions": rows}

    def note_disconnect(self, where: str, ts: str) -> None:
        self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, f"DISCONNECT_{where}", ts)
        for symbol, pos in list(self.positions.items()):
            if PositionState(pos["state"]) not in {PositionState.FLAT, PositionState.NO_ORDER}:
                self._set_symbol_state(symbol, PositionState.UNKNOWN, f"DISCONNECT_{where}", ts)

    def note_token_expiry(self, ts: str) -> None:
        self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "TOKEN_EXPIRY", ts)

    def note_station_loss(self, ts: str) -> None:
        self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "KABU_STATION_LOSS", ts)

    def note_market_bus(self, *, stale: bool, ts: str) -> None:
        if stale:
            self.set_mode(OperatingMode.ENTRY_DISABLED, "STALE_MARKET_BUS", ts)

    def note_heartbeat_stall(self, ts: str) -> None:
        self.set_mode(OperatingMode.ENTRY_DISABLED, "HEARTBEAT_STALL", ts)

    def confirmed_qty(self, symbol: str) -> int:
        pos = self.positions.get(symbol)
        if pos is None:
            return 0
        return int(pos["confirmed_qty"])

    def export_state(self) -> dict:
        return {
            "mode": self.mode.value,
            "durability_ok": self.durability_ok,
            "positions": {k: dict(v) for k, v in self.positions.items()},
            "submit_counts": {k: v.submit_count for k, v in self.intents.items()},
            "broker_order_ids": {k: v.broker_order_id for k, v in self.intents.items()},
            "confirmed": {k: int(v["confirmed_qty"]) for k, v in self.positions.items()},
        }

    def replay_journal(self, rows: list[dict]) -> None:
        for row in rows:
            event = str(row.get("event") or "")
            if event in {"ENTRY_INTENT", "EXIT_INTENT"}:
                intent = OrderIntent(
                    strategy_trade_id=str(row["strategy_trade_id"]),
                    signal_uid=str(row["signal_uid"]),
                    order_intent_id=str(row["order_intent_id"]),
                    side=str(row["side"]),
                    symbol=str(row["symbol"]),
                    intended_qty=int(row["intended_qty"]),
                    created_at=str(row["created_at"]),
                    activation_id=str(row["activation_id"]),
                )
                self.intents[intent.order_intent_id] = intent
                pos = self.positions.get(intent.symbol) or _blank_position(
                    intent.symbol, intent.strategy_trade_id, PositionState.NO_ORDER, ""
                )
                if intent.side == "BUY":
                    pos["state"] = PositionState.ENTRY_INTENT.value
                    pos["entry_intent_id"] = intent.order_intent_id
                else:
                    pos["state"] = PositionState.EXIT_INTENT.value
                    pos["exit_intent_id"] = intent.order_intent_id
                self.positions[intent.symbol] = pos
            elif event == "SUBMITTED":
                intent = self.intents[str(row["order_intent_id"])]
                intent.submit_count = 1
                intent.broker_order_id = str(row.get("broker_order_id") or "")
                nxt = PositionState.EXIT_SUBMITTING if intent.side == "SELL" else PositionState.SUBMITTING
                self.positions[intent.symbol]["state"] = nxt.value
            elif event == "ACK":
                intent = self.intents[str(row["order_intent_id"])]
                intent.broker_order_id = str(row.get("broker_order_id") or intent.broker_order_id)
                nxt = PositionState.EXIT_ACKNOWLEDGED if intent.side == "SELL" else PositionState.ACKNOWLEDGED
                self.positions[intent.symbol]["state"] = nxt.value
            elif event == "FILL":
                intent = self.intents[str(row["order_intent_id"])]
                exec_id = str(row["exec_id"])
                if exec_id in intent.exec_ids:
                    continue
                intent.exec_ids.add(exec_id)
                intent.confirmed_qty += int(row["qty"])
                intent.broker_order_id = str(row.get("broker_order_id") or intent.broker_order_id)
                pos = self.positions[intent.symbol]
                if intent.side == "BUY":
                    pos["entry_confirmed_qty"] = intent.confirmed_qty
                    pos["confirmed_qty"] = intent.confirmed_qty - int(pos.get("exit_confirmed_qty") or 0)
                    pos["state"] = (
                        PositionState.FILLED.value
                        if intent.confirmed_qty == intent.intended_qty
                        else PositionState.PARTIALLY_FILLED.value
                    )
                else:
                    pos["exit_confirmed_qty"] = intent.confirmed_qty
                    pos["confirmed_qty"] = int(pos["entry_confirmed_qty"]) - intent.confirmed_qty
                    pos["state"] = PositionState.FLAT.value if pos["confirmed_qty"] == 0 else PositionState.PARTIALLY_EXITED.value
            elif event == "REJECT":
                intent = self.intents[str(row["order_intent_id"])]
                intent.terminal = "REJECTED"
                if intent.side == "BUY":
                    self.positions[intent.symbol]["state"] = PositionState.NO_ORDER.value
                    self.positions[intent.symbol]["confirmed_qty"] = 0
            elif event == "MODE":
                self.mode = OperatingMode(str(row["to"]))
                self.mode_reason = str(row.get("reason") or "")
            elif event == "DURABILITY_FAILURE":
                self.durability_ok = False
                self.mode = OperatingMode.ENTRY_DISABLED

    def _submit_existing(self, intent: OrderIntent, submitting: PositionState, ts: str) -> dict:
        if intent.submit_count:
            self.duplicate_order_attempts += 1
            return {"ok": True, "reattached": True, "order_intent_id": intent.order_intent_id, "submit_count": intent.submit_count}
        submitted = {
            "event": "SUBMITTED",
            "order_intent_id": intent.order_intent_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "ts": ts,
        }
        if not self.journal.append(submitted):
            return self._durability_fail("SUBMITTED", ts)
        intent.submit_count = 1
        self._set_symbol_state(intent.symbol, submitting, "SUBMITTING", ts)
        try:
            result = self.broker.submit(intent)
        except BrokerTimeout:
            self._set_symbol_state(intent.symbol, PositionState.UNKNOWN, "SUBMIT_TIMEOUT", ts)
            return {"ok": False, "reason": "SUBMIT_TIMEOUT", "order_intent_id": intent.order_intent_id, "submit_count": 1}
        except BrokerUnreadable:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "BROKER_UNREADABLE", ts)
            self._set_symbol_state(intent.symbol, PositionState.UNKNOWN, "BROKER_UNREADABLE", ts)
            return {"ok": False, "reason": "BROKER_UNREADABLE", "order_intent_id": intent.order_intent_id, "submit_count": 1}
        status = str(getattr(result, "status", "") or "")
        if status == "REJECTED":
            return self.on_reject(intent.order_intent_id, ts)
        if status == "ACK_LOST":
            self._set_symbol_state(intent.symbol, PositionState.UNKNOWN, "ACK_LOST", ts)
            return {"ok": False, "reason": "ACK_LOST", "order_intent_id": intent.order_intent_id, "submit_count": 1}
        broker_order_id = str(getattr(result, "broker_order_id", "") or "")
        if broker_order_id:
            self.on_ack(intent.order_intent_id, broker_order_id, ts)
        return {"ok": True, "order_intent_id": intent.order_intent_id, "submit_count": intent.submit_count, "broker_order_id": intent.broker_order_id}

    def _build_intent(self, **kwargs) -> OrderIntent:
        oid = make_order_intent_id(
            activation_id=self.activation_id,
            strategy_trade_id=kwargs["strategy_trade_id"],
            signal_uid=kwargs["signal_uid"],
            side=kwargs["side"],
            symbol=kwargs["symbol"],
            intended_qty=kwargs["intended_qty"],
        )
        return OrderIntent(order_intent_id=oid, activation_id=self.activation_id, **kwargs)

    def _durability_fail(self, what: str, ts: str) -> dict:
        self.durability_ok = False
        self.set_mode(OperatingMode.ENTRY_DISABLED, "LEDGER_DURABILITY_FAILURE", ts)
        self._log("DURABILITY_FAILURE", reason=what, ts=ts)
        return {"ok": False, "reason": "LEDGER_DURABILITY_FAILURE"}

    def _set_symbol_state(self, symbol: str, nxt: PositionState, reason: str, ts: str) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        prev = PositionState(pos["state"])
        if prev == nxt and reason not in {"FILL"}:
            return
        pos["state"] = nxt.value
        self._transition(symbol, prev, nxt, reason, ts)

    def _transition(self, symbol: str, prev: PositionState, nxt: PositionState, reason: str, ts: str) -> None:
        self.transitions.append(
            {"symbol": symbol, "from": prev.value, "to": nxt.value, "reason": reason, "ts": ts, "mode": self.mode.value}
        )

    def _log(self, event: str, *, symbol: str = "", reason: str = "", ts: str = "", extra: Optional[dict] = None) -> None:
        row = {"event": event, "symbol": symbol, "reason": reason, "ts": ts}
        if extra:
            row.update(extra)
        if event == "MODE":
            self.journal.append({"event": "MODE", "from": extra.get("from") if extra else "", "to": extra.get("to") if extra else "", "reason": reason, "ts": ts})
        self.transitions.append(row)

    def _notify(self, payload: dict) -> None:
        if self.on_discord is None:
            return
        try:
            self.on_discord(payload)
        except Exception:
            self.discord_failures += 1

    def _safe_broker_view(self) -> BrokerView:
        try:
            return self.broker.view()
        except BrokerUnreadable as exc:
            return BrokerView(readable=False, reason=str(exc) or "BROKER_UNREADABLE")

    def _intent_for_broker(self, item: dict) -> Optional[OrderIntent]:
        oid = str(item.get("order_id") or "")
        intent_id = str(item.get("order_intent_id") or "")
        if intent_id and intent_id in self.intents:
            return self.intents[intent_id]
        for intent in self.intents.values():
            if intent.broker_order_id and intent.broker_order_id == oid:
                return intent
        return None

    def _reattach_broker_order(self, item: dict, ts: str) -> OrderIntent:
        side = str(item["side"])
        symbol = str(item["symbol"])
        qty = int(item.get("intended_qty") or item.get("cum_qty") or 0)
        intent = self._build_intent(
            strategy_trade_id=str(item.get("strategy_trade_id") or f"reattach-{item.get('order_id')}"),
            signal_uid=str(item.get("signal_uid") or f"reattach-{item.get('order_id')}"),
            side=side,
            symbol=symbol,
            intended_qty=qty,
            created_at=ts,
        )
        intent.broker_order_id = str(item.get("order_id") or "")
        intent.submit_count = 1
        intent.confirmed_qty = int(item.get("cum_qty") or 0)
        self.intents[intent.order_intent_id] = intent
        pos = self.positions.get(symbol) or _blank_position(symbol, intent.strategy_trade_id, PositionState.NO_ORDER, "")
        if side == "BUY":
            pos["entry_intent_id"] = intent.order_intent_id
            pos["entry_confirmed_qty"] = intent.confirmed_qty
            pos["confirmed_qty"] = intent.confirmed_qty
            pos["state"] = PositionState.ACKNOWLEDGED.value if intent.confirmed_qty == 0 else PositionState.PARTIALLY_FILLED.value
        else:
            pos["exit_intent_id"] = intent.order_intent_id
        self.positions[symbol] = pos
        self._log("REATTACH", symbol=symbol, reason=intent.broker_order_id, ts=ts)
        return intent

    def _promote_working(self, intent: OrderIntent, item: dict, ts: str) -> None:
        status = str(item.get("status") or "")
        if status not in {"WORKING", "ACKNOWLEDGED", "CANCEL_REQUESTED"}:
            return
        if intent.confirmed_qty >= intent.intended_qty and intent.intended_qty > 0:
            return
        current = PositionState(self.positions[intent.symbol]["state"])
        if current not in {
            PositionState.UNKNOWN,
            PositionState.SUBMITTING,
            PositionState.EXIT_SUBMITTING,
            PositionState.RECONCILIATION_REQUIRED,
            PositionState.ENTRY_INTENT,
            PositionState.EXIT_INTENT,
        }:
            return
        self._set_symbol_state(intent.symbol, _ack_state(intent), "REATTACH_WORKING", ts)

    def _adopt_order_cum(self, intent: OrderIntent, cum: int, ts: str) -> None:
        if cum < intent.confirmed_qty:
            self.set_mode(OperatingMode.RECONCILIATION_REQUIRED, "BROKER_CUM_BELOW_LOCAL", ts)
            self._set_symbol_state(intent.symbol, PositionState.RECONCILIATION_REQUIRED, "BROKER_CUM_BELOW_LOCAL", ts)
            return
        if cum == intent.confirmed_qty:
            return
        delta = cum - intent.confirmed_qty
        self.on_fill(intent.order_intent_id, f"reconcile-{intent.broker_order_id}-{cum}", delta, ts, broker_order_id=intent.broker_order_id)

    def _adopt_position_qty(self, symbol: str, broker_qty: int, ts: str) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            self.positions[symbol] = _blank_position(symbol, f"broker-{symbol}", PositionState.FILLED if broker_qty else PositionState.NO_ORDER, "")
            pos = self.positions[symbol]
        pos["confirmed_qty"] = int(broker_qty)
        pos["entry_confirmed_qty"] = max(int(pos["entry_confirmed_qty"]), int(broker_qty) + int(pos["exit_confirmed_qty"]))
        if broker_qty <= 0 and int(pos["exit_confirmed_qty"]) == 0 and PositionState(pos["state"]) in UNRESOLVED_ENTRY:
            return
        if broker_qty <= 0:
            pos["state"] = PositionState.FLAT.value if int(pos["exit_confirmed_qty"]) else PositionState.NO_ORDER.value
        elif int(pos["exit_confirmed_qty"]):
            pos["state"] = PositionState.PARTIALLY_EXITED.value
        else:
            pos["state"] = PositionState.FILLED.value
        self._log("ADOPT_BROKER_QTY", symbol=symbol, reason=str(broker_qty), ts=ts)

    def _parity(self, view: BrokerView) -> bool:
        symbols = set(view.positions) | set(self.positions)
        for symbol in symbols:
            if int(view.positions.get(symbol, 0)) != self.confirmed_qty(symbol):
                return False
        return True


def _ack_state(intent: OrderIntent) -> PositionState:
    done = intent.confirmed_qty == intent.intended_qty and intent.confirmed_qty > 0
    if intent.side == "SELL":
        if done:
            return PositionState.FLAT
        if intent.confirmed_qty > 0:
            return PositionState.PARTIALLY_EXITED
        return PositionState.EXIT_ACKNOWLEDGED
    if done:
        return PositionState.FILLED
    if intent.confirmed_qty > 0:
        return PositionState.PARTIALLY_FILLED
    return PositionState.ACKNOWLEDGED


def _blank_position(symbol: str, strategy_trade_id: str, state: PositionState, entry_intent_id: str) -> dict:
    return {
        "symbol": symbol,
        "strategy_trade_id": strategy_trade_id,
        "state": state.value,
        "confirmed_qty": 0,
        "entry_confirmed_qty": 0,
        "exit_confirmed_qty": 0,
        "entry_intent_id": entry_intent_id,
        "exit_intent_id": "",
    }


def _intent_row(intent: OrderIntent, event: str) -> dict:
    return {
        "event": event,
        "strategy_trade_id": intent.strategy_trade_id,
        "signal_uid": intent.signal_uid,
        "order_intent_id": intent.order_intent_id,
        "side": intent.side,
        "symbol": intent.symbol,
        "intended_qty": intent.intended_qty,
        "created_at": intent.created_at,
        "activation_id": intent.activation_id,
    }

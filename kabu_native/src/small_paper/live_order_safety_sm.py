"""
Phase687W2: Live Order Safety State Machine (dry-run only).

Explicit order lifecycle with idempotency, capital reservation, broker adapters,
reconciliation, and kill switch. Never sends real broker orders.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOT_SIZE = 100
MARGIN_LEVERAGE = 2.0
STALE_PRICE_AGE_SEC = 3.0  # reuse existing freshness threshold semantics
STALE_BOARD_AGE_SEC = 3.0


# ─── States ───────────────────────────────────────────────────────────────


class OrderLifecycleState(str, Enum):
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    PRECHECK_PENDING = "PRECHECK_PENDING"
    PRECHECK_REJECTED = "PRECHECK_REJECTED"
    CAPITAL_RESERVED = "CAPITAL_RESERVED"
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    SUBMIT_PENDING = "SUBMIT_PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    BROKER_REJECTED = "BROKER_REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


ENTRY_ALLOWED: dict[OrderLifecycleState, frozenset[OrderLifecycleState]] = {
    OrderLifecycleState.SIGNAL_RECEIVED: frozenset(
        {OrderLifecycleState.PRECHECK_PENDING, OrderLifecycleState.PRECHECK_REJECTED}
    ),
    OrderLifecycleState.PRECHECK_PENDING: frozenset(
        {
            OrderLifecycleState.PRECHECK_REJECTED,
            OrderLifecycleState.CAPITAL_RESERVED,
            OrderLifecycleState.ORDER_INTENT_CREATED,  # EXIT path (no capital reserve)
        }
    ),
    OrderLifecycleState.PRECHECK_REJECTED: frozenset(),
    OrderLifecycleState.CAPITAL_RESERVED: frozenset(
        {
            OrderLifecycleState.ORDER_INTENT_CREATED,
            OrderLifecycleState.PRECHECK_REJECTED,  # release path
        }
    ),
    OrderLifecycleState.ORDER_INTENT_CREATED: frozenset(
        {OrderLifecycleState.SUBMIT_PENDING, OrderLifecycleState.CANCELED}
    ),
    OrderLifecycleState.SUBMIT_PENDING: frozenset(
        {
            OrderLifecycleState.SUBMITTED,
            OrderLifecycleState.UNKNOWN,
            OrderLifecycleState.BROKER_REJECTED,
            OrderLifecycleState.CANCELED,
        }
    ),
    OrderLifecycleState.SUBMITTED: frozenset(
        {
            OrderLifecycleState.ACKNOWLEDGED,
            OrderLifecycleState.UNKNOWN,
            OrderLifecycleState.BROKER_REJECTED,
            OrderLifecycleState.CANCEL_PENDING,
        }
    ),
    OrderLifecycleState.ACKNOWLEDGED: frozenset(
        {
            OrderLifecycleState.PARTIALLY_FILLED,
            OrderLifecycleState.FILLED,
            OrderLifecycleState.CANCEL_PENDING,
            OrderLifecycleState.EXPIRED,
            OrderLifecycleState.BROKER_REJECTED,
        }
    ),
    OrderLifecycleState.PARTIALLY_FILLED: frozenset(
        {
            OrderLifecycleState.PARTIALLY_FILLED,
            OrderLifecycleState.FILLED,
            OrderLifecycleState.CANCEL_PENDING,
            OrderLifecycleState.EXPIRED,
        }
    ),
    OrderLifecycleState.FILLED: frozenset(),
    OrderLifecycleState.CANCEL_PENDING: frozenset(
        {
            OrderLifecycleState.CANCELED,
            OrderLifecycleState.FILLED,  # fill during cancel (broker race)
            OrderLifecycleState.PARTIALLY_FILLED,
            OrderLifecycleState.UNKNOWN,
        }
    ),
    OrderLifecycleState.CANCELED: frozenset(),
    OrderLifecycleState.BROKER_REJECTED: frozenset(),
    OrderLifecycleState.EXPIRED: frozenset(),
    OrderLifecycleState.UNKNOWN: frozenset(
        {
            OrderLifecycleState.ACKNOWLEDGED,
            OrderLifecycleState.CANCELED,
            OrderLifecycleState.BROKER_REJECTED,
            OrderLifecycleState.RECOVERY_REQUIRED,
            OrderLifecycleState.FILLED,
            OrderLifecycleState.PARTIALLY_FILLED,
        }
    ),
    OrderLifecycleState.RECOVERY_REQUIRED: frozenset(
        {
            OrderLifecycleState.ACKNOWLEDGED,
            OrderLifecycleState.CANCELED,
            OrderLifecycleState.FILLED,
            OrderLifecycleState.PARTIALLY_FILLED,
        }
    ),
}


def can_transition(src: OrderLifecycleState, dst: OrderLifecycleState) -> bool:
    if src == dst and src == OrderLifecycleState.PARTIALLY_FILLED:
        return True
    return dst in ENTRY_ALLOWED.get(src, frozenset())


def transition_matrix_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for src in OrderLifecycleState:
        allowed = ENTRY_ALLOWED.get(src, frozenset())
        for dst in OrderLifecycleState:
            rows.append(
                {
                    "from_state": src.value,
                    "to_state": dst.value,
                    "allowed": can_transition(src, dst),
                    "side": "ENTRY_OR_EXIT",
                }
            )
        # also list allowed explicitly for readability in CSV filters
        _ = allowed
    return rows


# ─── Idempotency ──────────────────────────────────────────────────────────


def make_idempotency_key(
    *,
    session_id: str,
    position_id: str,
    symbol: str,
    side: str,
    intent_sequence: int,
    exit_reason: str = "",
) -> str:
    side_u = str(side or "").upper()
    if side_u in ("SELL", "EXIT"):
        raw = f"{session_id}|{position_id}|{symbol}|EXIT|{exit_reason}|{intent_sequence}"
    else:
        raw = f"{session_id}|{position_id}|{symbol}|{side_u}|{intent_sequence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ─── Persistence (append-only) ─────────────────────────────────────────────


@dataclass
class AppendOnlyStore:
    output_dir: Path
    schema_version: str = "687W4.1"
    session_id: str = ""
    _seq: int = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _enrich(self, row: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out.setdefault("schema_version", self.schema_version)
        out.setdefault("session_id", self.session_id)
        out.setdefault("sequence", self._next_seq())
        out.setdefault("monotonic_sequence", out["sequence"])
        out.setdefault("event_time", out.get("timestamp") or _now())
        return out

    def _append(self, name: str, row: Mapping[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / name
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self._enrich(row), ensure_ascii=False) + "\n")

    def write_intent(self, row: Mapping[str, Any]) -> None:
        self._append("order_intents.jsonl", row)

    def write_state_event(self, row: Mapping[str, Any]) -> None:
        self._append("order_state_events.jsonl", row)

    def write_reconcile(self, row: Mapping[str, Any]) -> None:
        self._append("broker_reconciliation.jsonl", row)

    def write_capital_reservation(self, row: Mapping[str, Any]) -> None:
        self._append("capital_reservations.jsonl", row)

    def write_kill_switch(self, row: Mapping[str, Any]) -> None:
        self._append("kill_switch_events.jsonl", row)


# ─── Capital reservation ───────────────────────────────────────────────────


@dataclass
class Reservation:
    reservation_id: str
    symbol: str
    quantity: int
    capital_yen: float
    slot: bool = True
    released: bool = False
    filled_qty: int = 0


@dataclass
class CapitalLedger:
    reservations: dict[str, Reservation] = field(default_factory=dict)
    open_positions: dict[str, int] = field(default_factory=dict)  # symbol -> qty
    pending_by_symbol: dict[str, int] = field(default_factory=dict)

    def reserve(
        self,
        *,
        symbol: str,
        quantity: int,
        capital_yen: float,
        reservation_id: Optional[str] = None,
    ) -> Reservation:
        rid = reservation_id or uuid.uuid4().hex[:12]
        res = Reservation(
            reservation_id=rid,
            symbol=symbol,
            quantity=quantity,
            capital_yen=capital_yen,
        )
        self.reservations[rid] = res
        self.pending_by_symbol[symbol] = self.pending_by_symbol.get(symbol, 0) + quantity
        return res

    def apply_fill(self, reservation_id: str, fill_qty: int) -> None:
        res = self.reservations.get(reservation_id)
        if res is None or res.released:
            return
        fill_qty = max(0, min(int(fill_qty), res.quantity - res.filled_qty))
        res.filled_qty += fill_qty
        self.open_positions[res.symbol] = self.open_positions.get(res.symbol, 0) + fill_qty
        self.pending_by_symbol[res.symbol] = max(
            0, self.pending_by_symbol.get(res.symbol, 0) - fill_qty
        )

    def remaining_qty(self, reservation_id: str) -> int:
        res = self.reservations.get(reservation_id)
        if res is None or res.released:
            return 0
        return max(0, res.quantity - res.filled_qty)

    def release_remainder(self, reservation_id: str) -> float:
        res = self.reservations.get(reservation_id)
        if res is None or res.released:
            return 0.0
        remain = res.quantity - res.filled_qty
        if remain > 0:
            self.pending_by_symbol[res.symbol] = max(
                0, self.pending_by_symbol.get(res.symbol, 0) - remain
            )
        released_cap = res.capital_yen * (remain / max(1, res.quantity))
        if remain > 0 and res.filled_qty > 0:
            # partial cancel: filled qty is now position; remainder reservation freed
            res.quantity = res.filled_qty
        # cancel / full-fill / reject: no active capital reservation remains
        res.released = True
        return round(released_cap, 2)

    def release_all(self, reservation_id: str) -> float:
        res = self.reservations.get(reservation_id)
        if res is None or res.released:
            return 0.0
        remain = res.quantity - res.filled_qty
        self.pending_by_symbol[res.symbol] = max(
            0, self.pending_by_symbol.get(res.symbol, 0) - remain
        )
        released = res.capital_yen * (remain / max(1, res.quantity))
        res.released = True
        return round(released, 2)

    def active_reservation_count(self) -> int:
        return sum(1 for r in self.reservations.values() if not r.released)

    def leak_count(self) -> int:
        # leaked = unreleased with zero pending and zero open contribution expected after terminal
        return sum(
            1
            for r in self.reservations.values()
            if (not r.released) and r.filled_qty == 0 and self.pending_by_symbol.get(r.symbol, 0) <= 0
        )


# ─── Position sizing (dry-run calc only; policy unchanged) ─────────────────


def lot_round_down(qty: float, *, lot: int = LOT_SIZE) -> int:
    if qty <= 0:
        return 0
    return int(qty // lot) * lot


def dryrun_position_sizing(
    *,
    equity: float,
    available_buying_power: float,
    current_gross_exposure: float,
    current_symbol_exposure: float,
    price: float,
    max_position_ratio: float = 0.1,
    risk_per_trade: float = 0.01,
    stop_distance_pct: float = 0.02,
    baseline_qty: int = LOT_SIZE,
) -> dict[str, Any]:
    notional_100 = price * LOT_SIZE
    position_ratio = (notional_100 / equity) if equity > 0 else None
    max_notional = equity * max_position_ratio if equity > 0 else 0.0
    risk_budget = equity * risk_per_trade if equity > 0 else 0.0
    stop_dist = price * stop_distance_pct
    risk_qty = (risk_budget / stop_dist) if stop_dist > 0 else 0.0
    ratio_qty = (max_notional / price) if price > 0 else 0.0
    bp_qty = (available_buying_power / price) if price > 0 else 0.0
    raw = min(risk_qty, ratio_qty, bp_qty) if price > 0 else 0.0
    sized = lot_round_down(raw)
    required_margin = (price * sized / MARGIN_LEVERAGE) if sized > 0 else 0.0
    remaining = available_buying_power - required_margin
    return {
        "equity": equity,
        "available_buying_power": available_buying_power,
        "current_gross_exposure": current_gross_exposure,
        "current_symbol_exposure": current_symbol_exposure,
        "price_x_100": round(notional_100, 2),
        "position_ratio": round(position_ratio, 6) if position_ratio is not None else None,
        "max_position_ratio": max_position_ratio,
        "risk_per_trade": risk_per_trade,
        "stop_distance_pct": stop_distance_pct,
        "lot_rounded_quantity": sized,
        "required_margin": round(required_margin, 2),
        "remaining_capital_after_order": round(remaining, 2),
        "baseline_quantity": baseline_qty,
        "policy_unchanged": True,
        "order_allowed_by_lot": sized >= LOT_SIZE,
        "compare_vs_baseline": {
            "baseline": baseline_qty,
            "sized": sized,
            "delta": sized - baseline_qty,
        },
    }


# ─── Broker adapters ───────────────────────────────────────────────────────


@dataclass
class BrokerOrder:
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    filled_qty: int = 0
    status: str = "NEW"
    limit_price: Optional[float] = None


@dataclass
class BrokerAccount:
    equity: float = 1_000_000.0
    buying_power: float = 2_000_000.0
    online: bool = True
    token_valid: bool = True
    positions: dict[str, int] = field(default_factory=dict)
    open_orders: dict[str, BrokerOrder] = field(default_factory=dict)
    recent_executions: list[dict[str, Any]] = field(default_factory=list)


class BrokerAdapter:
    name: str = "base"

    def get_account_status(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_buying_power(self) -> float:
        raise NotImplementedError

    def get_positions(self) -> dict[str, int]:
        raise NotImplementedError

    def get_open_orders(self) -> dict[str, BrokerOrder]:
        raise NotImplementedError

    def submit_entry_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def submit_exit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_recent_executions(self) -> list[dict[str, Any]]:
        """Return recent execution snapshots. Default: empty (override in adapters)."""
        return []

    def reconcile_order(self, broker_order_id: str) -> dict[str, Any]:
        return self.get_order_status(broker_order_id)

    def emergency_flatten(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class MockBrokerAdapter(BrokerAdapter):
    """Configurable mock for fault injection."""

    name: str = "mock"
    account: BrokerAccount = field(default_factory=BrokerAccount)
    submit_count: int = 0
    actual_broker_submit_count: int = 0  # always 0 for mock/dryrun
    behavior: str = "full_fill"  # full_fill|partial|timeout_before|timeout_after|reject|dup_response|drop
    _seq: int = 0
    last_submit_intent: Optional[dict[str, Any]] = None

    def get_account_status(self) -> dict[str, Any]:
        return {
            "online": self.account.online,
            "token_valid": self.account.token_valid,
            "equity": self.account.equity,
            "buying_power": self.account.buying_power,
        }

    def get_buying_power(self) -> float:
        if not self.account.online:
            raise RuntimeError("broker_offline")
        return float(self.account.buying_power)

    def get_positions(self) -> dict[str, int]:
        return dict(self.account.positions)

    def get_open_orders(self) -> dict[str, BrokerOrder]:
        return dict(self.account.open_orders)

    def _new_id(self) -> str:
        self._seq += 1
        return f"MOCK-{self._seq:05d}"

    def submit_entry_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        self.submit_count += 1
        self.last_submit_intent = dict(intent)
        if self.behavior == "timeout_before":
            raise TimeoutError("timeout_before_submit")
        if self.behavior == "reject":
            return {"ok": False, "status": "REJECTED", "reason": "broker_reject"}
        if self.behavior == "insufficient_margin":
            return {"ok": False, "status": "REJECTED", "reason": "insufficient_margin"}
        oid = self._new_id()
        qty = int(intent.get("quantity") or LOT_SIZE)
        order = BrokerOrder(
            broker_order_id=oid,
            symbol=str(intent.get("symbol") or ""),
            side="BUY",
            quantity=qty,
            filled_qty=0,
            status="NEW",
            limit_price=_float(intent.get("limit_price")),
        )
        if self.behavior == "timeout_after":
            self.account.open_orders[oid] = order
            raise TimeoutError(f"timeout_after_submit:{oid}")
        if self.behavior == "drop":
            # pretend submit vanished — not in open orders
            return {"ok": True, "status": "UNKNOWN", "broker_order_id": oid, "dropped": True}
        self.account.open_orders[oid] = order
        if self.behavior == "partial":
            fill = max(LOT_SIZE // 10 * 3, 30) if qty >= 100 else max(1, qty // 3)
            fill = min(fill, qty)
            order.filled_qty = fill
            order.status = "PARTIAL"
            self.account.positions[order.symbol] = self.account.positions.get(order.symbol, 0) + fill
            self.account.recent_executions.append(
                {"broker_order_id": oid, "qty": fill, "symbol": order.symbol}
            )
            return {"ok": True, "status": "ACKNOWLEDGED", "broker_order_id": oid, "filled_qty": fill}
        if self.behavior == "dup_response":
            order.filled_qty = qty
            order.status = "FILLED"
            self.account.positions[order.symbol] = self.account.positions.get(order.symbol, 0) + qty
            del self.account.open_orders[oid]
            return {
                "ok": True,
                "status": "ACKNOWLEDGED",
                "broker_order_id": oid,
                "filled_qty": qty,
                "duplicate_response": True,
            }
        # full_fill
        order.filled_qty = qty
        order.status = "FILLED"
        self.account.positions[order.symbol] = self.account.positions.get(order.symbol, 0) + qty
        del self.account.open_orders[oid]
        self.account.recent_executions.append(
            {"broker_order_id": oid, "qty": qty, "symbol": order.symbol}
        )
        return {"ok": True, "status": "ACKNOWLEDGED", "broker_order_id": oid, "filled_qty": qty}

    def submit_exit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        self.submit_count += 1
        sym = str(intent.get("symbol") or "")
        qty = int(intent.get("quantity") or 0)
        held = self.account.positions.get(sym, 0)
        if qty > held:
            return {"ok": False, "status": "REJECTED", "reason": "exit_qty_exceeds_position"}
        oid = self._new_id()
        self.account.positions[sym] = held - qty
        if self.account.positions[sym] <= 0:
            self.account.positions.pop(sym, None)
        return {"ok": True, "status": "FILLED", "broker_order_id": oid, "filled_qty": qty}

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        order = self.account.open_orders.pop(broker_order_id, None)
        if order is None:
            return {"ok": False, "status": "NOT_FOUND"}
        return {
            "ok": True,
            "status": "CANCELED",
            "broker_order_id": broker_order_id,
            "filled_qty": order.filled_qty,
            "canceled_qty": order.quantity - order.filled_qty,
        }

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        order = self.account.open_orders.get(broker_order_id)
        if order is None:
            # check executions
            for ex in self.account.recent_executions:
                if ex.get("broker_order_id") == broker_order_id:
                    return {"ok": True, "status": "FILLED", "broker_order_id": broker_order_id, "filled_qty": ex.get("qty")}
            return {"ok": False, "status": "NOT_FOUND", "broker_order_id": broker_order_id}
        return {
            "ok": True,
            "status": order.status,
            "broker_order_id": broker_order_id,
            "filled_qty": order.filled_qty,
            "quantity": order.quantity,
        }

    def get_recent_executions(self) -> list[dict[str, Any]]:
        return list(self.account.recent_executions)

    def emergency_flatten(self) -> dict[str, Any]:
        closed = dict(self.account.positions)
        self.account.positions.clear()
        self.account.open_orders.clear()
        return {"ok": True, "closed": closed, "mode": "mock_only"}


@dataclass
class DryRunBrokerAdapter(MockBrokerAdapter):
    name: str = "dryrun"

    def submit_entry_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        out = super().submit_entry_order(intent)
        out["dry_run"] = True
        out["would_submit"] = True
        return out

    def submit_exit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        out = super().submit_exit_order(intent)
        out["dry_run"] = True
        out["would_submit"] = True
        return out


class KabuBrokerAdapter(BrokerAdapter):
    """Kabu Station adapter: read-only live API + HARD_FAIL on all mutations (Phase687W4)."""

    name: str = "kabu_readonly"

    def __init__(self, client: Any = None, token: str = "") -> None:
        self.client = client
        self.token = token or ""
        self.last_account_status = "UNKNOWN"
        self.last_error = ""
        self.last_latency_ms: dict[str, float] = {}
        self.actual_broker_submit_count = 0
        self.actual_broker_cancel_count = 0
        self._positions_cache: dict[str, int] = {}
        self._position_lots_cache: list[dict[str, Any]] = []
        self._orders_cache: dict[str, BrokerOrder] = {}
        self._exec_cache: list[dict[str, Any]] = []
        self._buying_power: Optional[float] = None
        self._cash_buying_power: Optional[float] = None
        self._margin_buying_power: Optional[float] = None
        self._equity: Optional[float] = None
        self._refreshed = False

    def _classify_exception(self, exc: BaseException) -> str:
        from small_paper.live_order_account_status import AccountReadStatus

        msg = str(exc).lower()
        name = type(exc).__name__.lower()
        if "timeout" in msg or isinstance(exc, TimeoutError) or "timeout" in name:
            return AccountReadStatus.TIMEOUT.value
        if "401" in msg or "unauthorized" in msg:
            return AccountReadStatus.AUTH_FAILED.value
        if "403" in msg or "auth" in msg and "fail" in msg:
            return AccountReadStatus.AUTH_FAILED.value
        if "token" in msg and ("expir" in msg or "invalid" in msg):
            return AccountReadStatus.TOKEN_EXPIRED.value
        if "token" in msg and ("request" in msg or "issue" in msg or "password" in msg):
            return AccountReadStatus.TOKEN_REQUEST_FAILED.value
        if "connection" in msg or "refused" in msg or "10061" in msg or "connect" in name:
            return AccountReadStatus.KABU_STATION_NOT_RUNNING.value
        if "404" in msg or "not supported" in msg:
            return AccountReadStatus.ENDPOINT_NOT_SUPPORTED.value
        if "503" in msg or "unavailable" in msg:
            return AccountReadStatus.ENDPOINT_UNAVAILABLE.value
        if "not json" in msg or "invalid" in msg:
            return AccountReadStatus.RESPONSE_INVALID.value
        # Do NOT map generic failures to weekend unavailable
        return AccountReadStatus.OFFLINE.value

    def refresh_readonly(self) -> str:
        """Fetch wallet/positions/orders. Never submits. Returns AccountReadStatus value."""
        from small_paper.live_order_account_status import AccountReadStatus

        self._refreshed = True
        if self.client is None:
            self.last_account_status = AccountReadStatus.CLIENT_NOT_CONFIGURED.value
            self.last_error = "client_not_configured"
            return self.last_account_status
        if not self.token:
            self.last_account_status = AccountReadStatus.TOKEN_REQUEST_FAILED.value
            self.last_error = "token_missing"
            return self.last_account_status
        try:
            cash, cash_ms = self.client.get_wallet_cash(token=self.token)
            margin, margin_ms = self.client.get_wallet_margin(token=self.token)
            positions, pos_ms = self.client.get_positions(token=self.token)
            orders, ord_ms = self.client.get_orders(token=self.token)
            self.last_latency_ms = {
                "wallet_cash_ms": float(cash_ms),
                "wallet_margin_ms": float(margin_ms),
                "positions_ms": float(pos_ms),
                "orders_ms": float(ord_ms),
            }
            stock_w = float(cash.get("StockAccountWallet") or cash.get("Cash") or 0.0)
            margin_w = float(margin.get("MarginAccountWallet") or margin.get("MarginAmount") or 0.0)
            self._cash_buying_power = stock_w
            self._margin_buying_power = margin_w
            self._equity = margin_w if margin_w > 0 else stock_w
            self._buying_power = margin_w if margin_w > 0 else stock_w

            pos_map: dict[str, int] = {}
            for p in positions or []:
                code = str(p.get("Symbol") or "")
                if not code:
                    continue
                qty = int(float(p.get("LeavesQty") or p.get("Qty") or 0))
                if qty <= 0:
                    continue
                sym = code if code.endswith(".T") else f"{code}.T"
                pos_map[sym] = pos_map.get(sym, 0) + qty
            self._positions_cache = pos_map
            # Phase687W5B: retain raw lot fields for capability/identity (read-only)
            self._position_lots_cache = [dict(p) for p in (positions or []) if isinstance(p, dict)]

            open_orders: dict[str, BrokerOrder] = {}
            for o in orders or []:
                oid = str(o.get("ID") or o.get("OrderId") or o.get("OrderID") or "")
                if not oid:
                    continue
                state = str(o.get("State") or o.get("OrderState") or "")
                code = str(o.get("Symbol") or "")
                sym = code if code.endswith(".T") else (f"{code}.T" if code else "")
                qty = int(float(o.get("OrderQty") or o.get("Qty") or 0))
                filled = int(float(o.get("CumQty") or o.get("FilledQty") or 0))
                side = "BUY" if str(o.get("Side") or "") in ("2", "BUY") else "SELL"
                open_orders[oid] = BrokerOrder(
                    broker_order_id=oid,
                    symbol=sym,
                    side=side,
                    quantity=qty,
                    filled_qty=filled,
                    status=state or "NEW",
                )
            self._orders_cache = open_orders
            self._exec_cache = list(self.client.extract_executions(orders or []))

            from datetime import datetime
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("Asia/Tokyo"))
            if self._buying_power is not None and self._buying_power <= 0:
                st = AccountReadStatus.ONLINE_ZERO_BALANCE.value
            elif now.weekday() >= 5:
                # Read succeeded on weekend/holiday → available, not "unavailable"
                st = AccountReadStatus.MARKET_CLOSED_READ_AVAILABLE.value
            elif not pos_map and not open_orders:
                st = AccountReadStatus.ONLINE_NO_POSITIONS.value
            elif not open_orders:
                st = AccountReadStatus.ONLINE_NO_ORDERS.value if pos_map else AccountReadStatus.ONLINE_VALID.value
            else:
                st = AccountReadStatus.ONLINE_VALID.value
            self.last_account_status = st
            self.last_error = ""
            return st
        except Exception as exc:
            st = self._classify_exception(exc)
            # Weekend + station down: may annotate, but keep primary class from exception
            if st == AccountReadStatus.KABU_STATION_NOT_RUNNING.value:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                if datetime.now(ZoneInfo("Asia/Tokyo")).weekday() >= 5:
                    # Secondary label only in error detail — status stays KABU_STATION_NOT_RUNNING
                    self.last_error = f"{type(exc).__name__};weekend_station_down"
                else:
                    self.last_error = type(exc).__name__
            else:
                self.last_error = type(exc).__name__
            self.last_account_status = st
            self._buying_power = None
            self._positions_cache = {}
            self._position_lots_cache = []
            self._orders_cache = {}
            self._exec_cache = []
            return st

    def get_account_status(self) -> dict[str, Any]:
        if not self._refreshed:
            self.refresh_readonly()
        online = self.last_account_status in (
            "ONLINE_VALID",
            "ONLINE_ZERO_BALANCE",
            "ONLINE_NO_POSITIONS",
            "ONLINE_NO_ORDERS",
            "MARKET_CLOSED_READ_AVAILABLE",
        )
        return {
            "online": online,
            "token_valid": bool(self.token)
            and self.last_account_status not in ("AUTH_FAILED", "TOKEN_EXPIRED"),
            "account_status": self.last_account_status,
            "error": self.last_error,
            "latency_ms": dict(self.last_latency_ms),
            "buying_power_present": self._buying_power is not None,
            "position_count": len(self._positions_cache or {}),
            "open_order_count": len(self._orders_cache or {}),
            "skeleton": self.client is None,
        }

    def get_buying_power(self) -> float:
        if not self._refreshed:
            self.refresh_readonly()
        if self._buying_power is None:
            raise RuntimeError(f"buying_power_unavailable:{self.last_account_status}")
        return float(self._buying_power)

    def get_positions(self) -> dict[str, int]:
        if not self._refreshed:
            self.refresh_readonly()
        return dict(self._positions_cache or {})

    def get_position_lots_raw(self) -> list[dict[str, Any]]:
        """Read-only raw /positions rows for W5B identity (caller must mask HoldIDs in artifacts)."""
        if not self._refreshed:
            self.refresh_readonly()
        return [dict(x) for x in (self._position_lots_cache or [])]

    def get_cash_buying_power(self) -> Optional[float]:
        if not self._refreshed:
            self.refresh_readonly()
        return self._cash_buying_power

    def get_margin_buying_power(self) -> Optional[float]:
        if not self._refreshed:
            self.refresh_readonly()
        return self._margin_buying_power

    def get_open_orders(self) -> dict[str, BrokerOrder]:
        if not self._refreshed:
            self.refresh_readonly()
        return dict(self._orders_cache or {})

    def get_recent_executions(self) -> list[dict[str, Any]]:
        if not self._refreshed:
            self.refresh_readonly()
        out = []
        for ex in self._exec_cache or []:
            out.append(
                {
                    "symbol": str(ex.get("Symbol") or ""),
                    "qty": ex.get("Qty") or ex.get("LeaveQty"),
                    "price": ex.get("Price"),
                    "execution_id_present": bool(ex.get("ExecutionID")),
                }
            )
        return out

    def get_order_status(self, broker_order_id: str) -> dict[str, Any]:
        if not self._refreshed:
            self.refresh_readonly()
        order = (self._orders_cache or {}).get(broker_order_id)
        if order is None:
            return {"ok": False, "status": "NOT_FOUND", "broker_order_id": broker_order_id}
        return {
            "ok": True,
            "status": order.status,
            "broker_order_id": broker_order_id,
            "filled_qty": order.filled_qty,
            "quantity": order.quantity,
        }

    def reconcile_order(self, broker_order_id: str) -> dict[str, Any]:
        return self.get_order_status(broker_order_id)

    def submit_entry_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("HARD_FAIL: KabuBrokerAdapter submit_entry_order forbidden")

    def submit_exit_order(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("HARD_FAIL: KabuBrokerAdapter submit_exit_order forbidden")

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        raise RuntimeError("HARD_FAIL: KabuBrokerAdapter cancel_order forbidden")

    def emergency_flatten(self) -> dict[str, Any]:
        raise RuntimeError("HARD_FAIL: KabuBrokerAdapter emergency_flatten forbidden")


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


# ─── Engine ────────────────────────────────────────────────────────────────


@dataclass
class SafetyOrder:
    order_id: str
    idempotency_key: str
    side: str  # BUY / SELL
    symbol: str
    quantity: int
    state: OrderLifecycleState
    session_id: str
    position_id: str
    intent_sequence: int = 1
    reservation_id: str = ""
    broker_order_id: str = ""
    filled_qty: int = 0
    exit_reason: str = ""
    reject_reason: str = ""
    created_at: str = ""
    illegal_transitions: list[str] = field(default_factory=list)


@dataclass
class LiveOrderSafetyEngine:
    broker: BrokerAdapter
    store: AppendOnlyStore
    session_id: str
    config: Any = None
    ledger: CapitalLedger = field(default_factory=CapitalLedger)
    orders: dict[str, SafetyOrder] = field(default_factory=dict)
    by_idempotency: dict[str, str] = field(default_factory=dict)
    kill_switch: bool = False
    kill_reasons: list[str] = field(default_factory=list)
    entry_blocked: bool = False
    recovery_required: bool = False
    discord_events: list[dict[str, Any]] = field(default_factory=list)
    discord_failures: int = 0
    illegal_transition_count: int = 0
    duplicate_order_count: int = 0
    intent_seq: int = 0
    daily_realized_loss: float = 0.0
    daily_loss_threshold: Optional[float] = None  # structure only; not set in production
    jsonl_write_fail: bool = False
    last_journal_error: str = ""
    actual_broker_cancel_count: int = 0

    def _notify(self, kind: str, payload: Mapping[str, Any]) -> None:
        msg = {
            "timestamp": _now(),
            "kind": kind,
            "dry_run": True,
            "label": f"[DRY-RUN] {kind}",
            **dict(payload),
        }
        try:
            # Discord failure must not affect state transitions
            if getattr(self, "_force_discord_fail", False):
                raise RuntimeError("discord_failure")
            self.discord_events.append(msg)
        except Exception:
            self.discord_failures += 1

    def _persist_intent(self, row: Mapping[str, Any]) -> None:
        if self.jsonl_write_fail:
            raise OSError("jsonl_write_failure")
        self.store.write_intent(row)

    def _persist_state(self, row: Mapping[str, Any]) -> None:
        if self.jsonl_write_fail:
            raise OSError("jsonl_write_failure")
        self.store.write_state_event(row)

    def activate_kill_switch(self, reason: str) -> None:
        self.kill_switch = True
        self.entry_blocked = True
        self.kill_reasons.append(reason)
        self._notify("KILL SWITCH", {"reason": reason})
        try:
            self.store.write_kill_switch(
                {
                    "timestamp": _now(),
                    "reason": reason,
                    "dry_run": True,
                    "event": "activate",
                }
            )
        except OSError:
            self.recovery_required = True
            self.entry_blocked = True
            self.last_journal_error = "kill_switch_journal_write_failure"

    def transition(self, order: SafetyOrder, new_state: OrderLifecycleState, *, detail: str = "") -> bool:
        if not can_transition(order.state, new_state):
            self.illegal_transition_count += 1
            msg = f"{order.state.value}->{new_state.value}"
            order.illegal_transitions.append(msg)
            self.store.write_state_event(
                {
                    "timestamp": _now(),
                    "order_id": order.order_id,
                    "event": "ILLEGAL_TRANSITION",
                    "from": order.state.value,
                    "to": new_state.value,
                    "detail": detail,
                    "dry_run": True,
                }
            )
            return False
        old = order.state
        order.state = new_state
        try:
            self._persist_state(
                {
                    "timestamp": _now(),
                    "order_id": order.order_id,
                    "idempotency_key": order.idempotency_key,
                    "symbol": order.symbol,
                    "from": old.value,
                    "to": new_state.value,
                    "filled_qty": order.filled_qty,
                    "quantity": order.quantity,
                    "detail": detail,
                    "dry_run": True,
                }
            )
        except OSError:
            # persistence failure must not roll back in-memory transition for dry-run engine,
            # but is recorded
            self.discord_failures += 1
        return True

    def precheck(self, *, symbol: str, price: float, ctx: Mapping[str, Any]) -> tuple[bool, str]:
        if bool(getattr(self.config, "live_trading_enabled", False)):
            return False, "live_trading_enabled"
        if bool(getattr(self.config, "order_enabled", False)):
            return False, "order_enabled"
        if not bool(getattr(self.config, "dry_run", True)):
            return False, "dry_run_required"
        if self.kill_switch or self.entry_blocked or self.recovery_required:
            return False, "kill_switch_or_recovery"
        st = self.broker.get_account_status()
        if not st.get("online"):
            return False, "broker_offline"
        if not st.get("token_valid"):
            return False, "token_invalid"
        price_age = _float(ctx.get("price_age_sec"))
        board_age = _float(ctx.get("board_age_sec"))
        if price_age is not None and price_age > STALE_PRICE_AGE_SEC:
            return False, "stale_price"
        if board_age is not None and board_age > STALE_BOARD_AGE_SEC:
            return False, "stale_board"
        if not ctx.get("symbol_registered", True):
            return False, "symbol_not_registered"
        if self.ledger.open_positions.get(symbol, 0) > 0 and ctx.get("allow_pyramid") is not True:
            return False, "same_symbol_position"
        if self.ledger.pending_by_symbol.get(symbol, 0) > 0:
            return False, "pending_order"
        cap_limit = int(getattr(self.config, "max_concurrent_positions", 3) or 3)
        open_n = len([q for q in self.ledger.open_positions.values() if q > 0])
        pending_n = len([q for q in self.ledger.pending_by_symbol.values() if q > 0])
        if open_n + pending_n >= cap_limit:
            return False, "position_cap"
        try:
            bp = self.broker.get_buying_power()
        except Exception:
            return False, "buying_power_unavailable"
        if bp <= 0:
            return False, "buying_power_zero"
        required = price * LOT_SIZE / MARGIN_LEVERAGE
        if bp < required:
            return False, "insufficient_margin"
        if self.daily_loss_threshold is not None and self.daily_realized_loss >= self.daily_loss_threshold:
            self.activate_kill_switch("daily_loss_threshold")
            return False, "daily_loss_threshold"
        qty = int(ctx.get("quantity") or LOT_SIZE)
        if qty < LOT_SIZE or qty % LOT_SIZE != 0:
            return False, "lot_size"
        return True, ""

    def handle_entry_signal(
        self,
        *,
        symbol: str,
        price: float,
        position_id: str,
        ctx: Optional[Mapping[str, Any]] = None,
    ) -> SafetyOrder:
        ctx = dict(ctx or {})
        ctx.setdefault("quantity", LOT_SIZE)
        self.intent_seq += 1
        key = make_idempotency_key(
            session_id=self.session_id,
            position_id=position_id,
            symbol=symbol,
            side="BUY",
            intent_sequence=1,  # same ENTRY signal → same key
        )
        if key in self.by_idempotency:
            self.duplicate_order_count += 1
            existing = self.orders[self.by_idempotency[key]]
            self._notify("ORDER PRECHECK BLOCK", {"reason": "duplicate_entry_signal", "symbol": symbol})
            return existing

        order = SafetyOrder(
            order_id=uuid.uuid4().hex[:12],
            idempotency_key=key,
            side="BUY",
            symbol=symbol,
            quantity=int(ctx["quantity"]),
            state=OrderLifecycleState.SIGNAL_RECEIVED,
            session_id=self.session_id,
            position_id=position_id,
            intent_sequence=1,
            created_at=_now(),
        )
        self.orders[order.order_id] = order
        self.by_idempotency[key] = order.order_id
        self.transition(order, OrderLifecycleState.PRECHECK_PENDING, detail="entry_signal")
        self._notify("ORDER INTENT", {"symbol": symbol, "order_id": order.order_id})

        ok, reason = self.precheck(symbol=symbol, price=price, ctx=ctx)
        if not ok:
            order.reject_reason = reason
            self.transition(order, OrderLifecycleState.PRECHECK_REJECTED, detail=reason)
            self._notify("ORDER PRECHECK BLOCK", {"reason": reason, "symbol": symbol})
            return order

        capital = price * order.quantity / MARGIN_LEVERAGE
        res = self.ledger.reserve(symbol=symbol, quantity=order.quantity, capital_yen=capital)
        order.reservation_id = res.reservation_id
        try:
            self.store.write_capital_reservation(
                {
                    "timestamp": _now(),
                    "event": "reserve",
                    "reservation_id": res.reservation_id,
                    "symbol": symbol,
                    "quantity": order.quantity,
                    "capital_yen": capital,
                    "position_id": position_id,
                    "intent_id": order.order_id,
                    "idempotency_key": key,
                    "dry_run": True,
                }
            )
        except OSError:
            self.ledger.release_all(res.reservation_id)
            order.reject_reason = "jsonl_write_failure"
            self.activate_kill_switch("journal_write_failure")
            self.transition(order, OrderLifecycleState.PRECHECK_REJECTED, detail="journal_write_failure")
            return order
        self.transition(order, OrderLifecycleState.CAPITAL_RESERVED, detail="reserved")
        self.transition(order, OrderLifecycleState.ORDER_INTENT_CREATED, detail="intent")
        try:
            self._persist_intent(
                {
                    "timestamp": _now(),
                    "order_id": order.order_id,
                    "idempotency_key": key,
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": order.quantity,
                    "price": price,
                    "dry_run": True,
                    "session_id": self.session_id,
                    "position_id": position_id,
                }
            )
        except OSError:
            self.ledger.release_all(res.reservation_id)
            order.reject_reason = "jsonl_write_failure"
            self.transition(order, OrderLifecycleState.CANCELED, detail="jsonl_write_failure")
            return order

        self.transition(order, OrderLifecycleState.SUBMIT_PENDING, detail="submit_pending")
        try:
            resp = self.broker.submit_entry_order(
                {
                    "symbol": symbol,
                    "quantity": order.quantity,
                    "limit_price": price,
                    "idempotency_key": key,
                    "dry_run": True,
                }
            )
        except TimeoutError as exc:
            msg = str(exc)
            if "before" in msg:
                self.ledger.release_all(res.reservation_id)
                self.transition(order, OrderLifecycleState.BROKER_REJECTED, detail="timeout_before_submit")
                self._notify("BROKER REJECT", {"reason": "timeout_before_submit"})
                return order
            # after submit → UNKNOWN, reconcile (no blind resend)
            if ":" in msg:
                order.broker_order_id = msg.rsplit(":", 1)[-1]
            else:
                opens = self.broker.get_open_orders()
                if opens:
                    order.broker_order_id = next(iter(opens.keys()))
            self.transition(order, OrderLifecycleState.UNKNOWN, detail="timeout_after_submit")
            self._notify("ORDER SUBMITTED DRYRUN", {"status": "UNKNOWN"})
            return order

        if not resp.get("ok"):
            self.ledger.release_all(res.reservation_id)
            order.reject_reason = str(resp.get("reason") or "broker_reject")
            self.transition(order, OrderLifecycleState.BROKER_REJECTED, detail=order.reject_reason)
            self._notify("BROKER REJECT", {"reason": order.reject_reason})
            return order

        if resp.get("status") == "UNKNOWN" or resp.get("dropped"):
            order.broker_order_id = str(resp.get("broker_order_id") or "")
            self.transition(order, OrderLifecycleState.UNKNOWN, detail="submit_unknown")
            return order

        order.broker_order_id = str(resp.get("broker_order_id") or "")
        self.transition(order, OrderLifecycleState.SUBMITTED, detail="submitted")
        self.transition(order, OrderLifecycleState.ACKNOWLEDGED, detail="ack")
        self._notify("ORDER ACK", {"broker_order_id": order.broker_order_id})
        self._notify("ORDER SUBMITTED DRYRUN", {"broker_order_id": order.broker_order_id})

        fill = int(resp.get("filled_qty") or 0)
        if fill > 0:
            self._apply_entry_fill(order, fill)
        if resp.get("duplicate_response"):
            # idempotent: ignore second fill application
            pass
        return order

    def _apply_entry_fill(self, order: SafetyOrder, fill_qty: int) -> None:
        prev = order.filled_qty
        order.filled_qty = min(order.quantity, order.filled_qty + fill_qty)
        delta = order.filled_qty - prev
        if delta > 0 and order.reservation_id:
            self.ledger.apply_fill(order.reservation_id, delta)
            try:
                self.store.write_capital_reservation(
                    {
                        "timestamp": _now(),
                        "event": "apply_fill",
                        "reservation_id": order.reservation_id,
                        "symbol": order.symbol,
                        "fill_qty": delta,
                        "filled_qty": order.filled_qty,
                        "quantity": order.quantity,
                        "intent_id": order.order_id,
                        "idempotency_key": order.idempotency_key,
                        "dry_run": True,
                    }
                )
            except OSError:
                self.activate_kill_switch("journal_write_failure")
        if order.filled_qty >= order.quantity:
            self.transition(order, OrderLifecycleState.FILLED, detail="full_fill")
            if order.reservation_id:
                self.ledger.release_remainder(order.reservation_id)
                try:
                    self.store.write_capital_reservation(
                        {
                            "timestamp": _now(),
                            "event": "release_remainder",
                            "reservation_id": order.reservation_id,
                            "symbol": order.symbol,
                            "intent_id": order.order_id,
                            "idempotency_key": order.idempotency_key,
                            "dry_run": True,
                        }
                    )
                except OSError:
                    self.activate_kill_switch("journal_write_failure")
            self._notify("FILL", {"filled_qty": order.filled_qty})
        else:
            self.transition(order, OrderLifecycleState.PARTIALLY_FILLED, detail=f"partial:{order.filled_qty}")
            self._notify("PARTIAL FILL", {"filled_qty": order.filled_qty})

    def additional_fill(self, order_id: str, fill_qty: int) -> SafetyOrder:
        order = self.orders[order_id]
        self._apply_entry_fill(order, fill_qty)
        return order

    def cancel(self, order_id: str) -> SafetyOrder:
        order = self.orders[order_id]
        if order.state in (
            OrderLifecycleState.FILLED,
            OrderLifecycleState.CANCELED,
            OrderLifecycleState.BROKER_REJECTED,
            OrderLifecycleState.PRECHECK_REJECTED,
        ):
            return order
        self.transition(order, OrderLifecycleState.CANCEL_PENDING, detail="cancel_requested")
        if order.broker_order_id:
            resp = self.broker.cancel_order(order.broker_order_id)
            if resp.get("status") == "FILLED":
                # fill during cancel
                self._apply_entry_fill(order, int(resp.get("filled_qty") or 0))
                return order
        if order.reservation_id:
            self.ledger.release_remainder(order.reservation_id)
        self.transition(order, OrderLifecycleState.CANCELED, detail="canceled")
        self._notify("CANCEL", {"order_id": order.order_id})
        return order

    def handle_exit_signal(
        self,
        *,
        symbol: str,
        quantity: Optional[int] = None,
        exit_reason: str = "trailing_mfe_exit",
        position_id: str = "",
    ) -> SafetyOrder:
        key = make_idempotency_key(
            session_id=self.session_id,
            position_id=position_id or symbol,
            symbol=symbol,
            side="EXIT",
            intent_sequence=1,
            exit_reason=exit_reason,
        )
        if key in self.by_idempotency:
            self.duplicate_order_count += 1
            return self.orders[self.by_idempotency[key]]

        held = self.ledger.open_positions.get(symbol, 0)
        qty = int(quantity if quantity is not None else held)
        if qty > held:
            qty = held
        if qty <= 0:
            order = SafetyOrder(
                order_id=uuid.uuid4().hex[:12],
                idempotency_key=key,
                side="SELL",
                symbol=symbol,
                quantity=0,
                state=OrderLifecycleState.PRECHECK_REJECTED,
                session_id=self.session_id,
                position_id=position_id or symbol,
                reject_reason="no_position",
                created_at=_now(),
            )
            self.orders[order.order_id] = order
            self.by_idempotency[key] = order.order_id
            return order
        self.intent_seq += 1
        order = SafetyOrder(
            order_id=uuid.uuid4().hex[:12],
            idempotency_key=key,
            side="SELL",
            symbol=symbol,
            quantity=qty,
            state=OrderLifecycleState.SIGNAL_RECEIVED,
            session_id=self.session_id,
            position_id=position_id or symbol,
            exit_reason=exit_reason,
            created_at=_now(),
        )
        self.orders[order.order_id] = order
        self.by_idempotency[key] = order.order_id
        self.transition(order, OrderLifecycleState.PRECHECK_PENDING)
        self.transition(order, OrderLifecycleState.ORDER_INTENT_CREATED)
        self.transition(order, OrderLifecycleState.SUBMIT_PENDING)
        resp = self.broker.submit_exit_order(
            {"symbol": symbol, "quantity": qty, "exit_reason": exit_reason, "dry_run": True}
        )
        if not resp.get("ok"):
            order.reject_reason = str(resp.get("reason") or "exit_reject")
            self.transition(order, OrderLifecycleState.BROKER_REJECTED, detail=order.reject_reason)
            return order
        order.broker_order_id = str(resp.get("broker_order_id") or "")
        order.filled_qty = int(resp.get("filled_qty") or qty)
        self.ledger.open_positions[symbol] = max(0, self.ledger.open_positions.get(symbol, 0) - order.filled_qty)
        if self.ledger.open_positions.get(symbol, 0) <= 0:
            self.ledger.open_positions.pop(symbol, None)
        self.transition(order, OrderLifecycleState.SUBMITTED)
        self.transition(order, OrderLifecycleState.ACKNOWLEDGED)
        self.transition(order, OrderLifecycleState.FILLED, detail="exit_filled")
        self._notify("FILL", {"side": "SELL", "filled_qty": order.filled_qty, "exit_reason": exit_reason})
        return order

    def reconcile_unknown(self, order_id: str) -> SafetyOrder:
        order = self.orders[order_id]
        if order.state != OrderLifecycleState.UNKNOWN:
            return order
        # Never blind-resend; broker lookup only
        if not order.broker_order_id:
            self.transition(order, OrderLifecycleState.RECOVERY_REQUIRED, detail="missing_broker_id")
            self.recovery_required = True
            self.entry_blocked = True
            self._notify("RECOVERY REQUIRED", {"order_id": order_id})
            return order
        status = self.broker.reconcile_order(order.broker_order_id)
        st = str(status.get("status") or "")
        if st in ("ACKNOWLEDGED", "NEW", "PARTIAL"):
            self.transition(order, OrderLifecycleState.ACKNOWLEDGED, detail="reconciled")
            fill = int(status.get("filled_qty") or 0)
            if fill > 0:
                self._apply_entry_fill(order, fill)
        elif st == "FILLED":
            self.transition(order, OrderLifecycleState.ACKNOWLEDGED, detail="reconciled_filled")
            self._apply_entry_fill(order, int(status.get("filled_qty") or order.quantity))
        elif st in ("CANCELED", "CANCELLED"):
            if order.reservation_id:
                self.ledger.release_all(order.reservation_id)
            self.transition(order, OrderLifecycleState.CANCELED, detail="reconciled_canceled")
        elif st == "NOT_FOUND":
            if order.reservation_id:
                self.ledger.release_all(order.reservation_id)
            self.transition(order, OrderLifecycleState.CANCELED, detail="reconciled_not_found")
        else:
            self.transition(order, OrderLifecycleState.RECOVERY_REQUIRED, detail=st)
            self.recovery_required = True
            self.entry_blocked = True
            self._notify("RECONCILIATION ERROR", {"status": st})
        self.store.write_reconcile(
            {
                "timestamp": _now(),
                "order_id": order_id,
                "broker_order_id": order.broker_order_id,
                "result": st,
                "dry_run": True,
            }
        )
        return order

    def startup_reconciliation(
        self,
        *,
        local_positions: Mapping[str, int],
        local_pending: Mapping[str, str],
    ) -> dict[str, Any]:
        broker_pos = self.broker.get_positions()
        broker_orders = self.broker.get_open_orders()
        diffs: list[dict[str, Any]] = []
        for sym, qty in local_positions.items():
            bq = broker_pos.get(sym, 0)
            if bq != qty:
                diffs.append(
                    {
                        "type": "QUANTITY_MISMATCH",
                        "symbol": sym,
                        "local": qty,
                        "broker": bq,
                    }
                )
        for sym, qty in broker_pos.items():
            if sym not in local_positions:
                diffs.append({"type": "BROKER_ONLY_POSITION", "symbol": sym, "broker": qty})
        for sym, qty in local_positions.items():
            if sym not in broker_pos and qty > 0:
                diffs.append({"type": "LOCAL_ONLY_POSITION", "symbol": sym, "local": qty})
        for oid, order in broker_orders.items():
            if oid not in {o.broker_order_id for o in self.orders.values()}:
                diffs.append(
                    {
                        "type": "BROKER_ONLY_ORDER",
                        "broker_order_id": oid,
                        "symbol": order.symbol,
                    }
                )
        for oid, _ in local_pending.items():
            if oid not in broker_orders:
                diffs.append({"type": "LOCAL_ONLY_ORDER", "local_order_id": oid})

        # API unavailable classification (no silent mock fallback)
        try:
            st = self.broker.get_account_status()
            if not st.get("online") and st.get("account_status"):
                diffs.append(
                    {
                        "type": "API_UNAVAILABLE",
                        "account_status": st.get("account_status"),
                        "error": st.get("error"),
                    }
                )
            elif st.get("buying_power_present") is False and st.get("skeleton"):
                diffs.append({"type": "BUYING_POWER_UNKNOWN", "account_status": st.get("account_status")})
        except Exception as exc:
            diffs.append({"type": "API_UNAVAILABLE", "error": type(exc).__name__})

        mode = "MATCH"
        if diffs:
            self.recovery_required = True
            self.entry_blocked = True
            mode = "RECOVERY_REQUIRED"
            # Broker-only / mismatch → exit-only
            types = {d.get("type") for d in diffs}
            if types & {
                "BROKER_ONLY_POSITION",
                "LOCAL_ONLY_POSITION",
                "QUANTITY_MISMATCH",
                "BROKER_ONLY_ORDER",
                "LOCAL_ONLY_ORDER",
            }:
                mode = "EXIT_ONLY"
            self._notify("RECOVERY REQUIRED", {"diffs": len(diffs), "mode": mode})
            self._notify("RECONCILIATION ERROR", {"diffs": diffs[:10]})
        for d in diffs:
            self.store.write_reconcile({"timestamp": _now(), "dry_run": True, **d})
        return {
            "diff_count": len(diffs),
            "diffs": diffs,
            "entry_blocked": self.entry_blocked,
            "recovery_required": self.recovery_required,
            "mode": mode if diffs else "MATCH",
            "classification": mode if diffs else "MATCH",
        }

    def actual_broker_submit_count(self) -> int:
        return int(getattr(self.broker, "actual_broker_submit_count", 0))

    # Interface aliases (design spec names)
    def receive_entry_signal(self, **kwargs: Any) -> SafetyOrder:
        return self.handle_entry_signal(**kwargs)

    def receive_exit_signal(self, **kwargs: Any) -> SafetyOrder:
        return self.handle_exit_signal(**kwargs)

    def reconcile(self, order_id: str) -> SafetyOrder:
        return self.reconcile_unknown(order_id)

    def release_reservation(self, reservation_id: str) -> float:
        return self.ledger.release_all(reservation_id)

    def reserve_capital(self, *, symbol: str, quantity: int, capital_yen: float) -> Reservation:
        return self.ledger.reserve(symbol=symbol, quantity=quantity, capital_yen=capital_yen)

    def restore_from_journal(self) -> dict[str, Any]:
        """Rebuild orders, reservations, positions, kill-switch from append-only journals.

        Never re-submits broker orders. Never calls write adapter methods.
        """
        out_dir = self.store.output_dir
        intents_path = out_dir / "order_intents.jsonl"
        state_path = out_dir / "order_state_events.jsonl"
        capital_path = out_dir / "capital_reservations.jsonl"
        kill_path = out_dir / "kill_switch_events.jsonl"

        automatic_resubmit_count = 0
        duplicate_intent_count = 0
        journal_issues: list[str] = []
        max_seq = 0

        def _bump_seq(row: Mapping[str, Any]) -> None:
            nonlocal max_seq
            seq = row.get("sequence")
            if isinstance(seq, int):
                max_seq = max(max_seq, seq)

        # --- intents ---
        if intents_path.is_file():
            for line in intents_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    journal_issues.append("malformed_intent")
                    continue
                _bump_seq(row)
                oid = str(row.get("order_id") or "")
                if not oid:
                    continue
                key = str(row.get("idempotency_key") or "")
                if key and key in self.by_idempotency and self.by_idempotency[key] != oid:
                    duplicate_intent_count += 1
                    journal_issues.append(f"duplicate_idempotency:{key}")
                    continue
                if oid in self.orders:
                    duplicate_intent_count += 1
                    continue
                order = SafetyOrder(
                    order_id=oid,
                    idempotency_key=key,
                    side=str(row.get("side") or "BUY"),
                    symbol=str(row.get("symbol") or ""),
                    quantity=int(row.get("quantity") or 0),
                    state=OrderLifecycleState.ORDER_INTENT_CREATED,
                    session_id=str(row.get("session_id") or self.session_id),
                    position_id=str(row.get("position_id") or ""),
                    reservation_id=str(row.get("reservation_id") or ""),
                    broker_order_id=str(row.get("broker_order_id") or ""),
                    filled_qty=int(row.get("filled_qty") or 0),
                    created_at=str(row.get("timestamp") or row.get("event_time") or ""),
                )
                self.orders[oid] = order
                if key:
                    self.by_idempotency[key] = oid

        # --- state events (final state by event order) ---
        if state_path.is_file():
            for line in state_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    journal_issues.append("malformed_state")
                    continue
                _bump_seq(row)
                if row.get("event") == "ILLEGAL_TRANSITION":
                    continue
                oid = str(row.get("order_id") or "")
                if not oid:
                    # allow lookup by idempotency only
                    key = str(row.get("idempotency_key") or "")
                    oid = self.by_idempotency.get(key, "")
                if not oid:
                    continue
                to_state = str(row.get("to") or row.get("state") or "")
                if oid not in self.orders:
                    key = str(row.get("idempotency_key") or "")
                    self.orders[oid] = SafetyOrder(
                        order_id=oid,
                        idempotency_key=key,
                        side=str(row.get("side") or "BUY"),
                        symbol=str(row.get("symbol") or ""),
                        quantity=int(row.get("quantity") or 0),
                        state=OrderLifecycleState.SIGNAL_RECEIVED,
                        session_id=self.session_id,
                        position_id=str(row.get("position_id") or ""),
                        reservation_id=str(row.get("reservation_id") or ""),
                    )
                    if key:
                        self.by_idempotency[key] = oid
                order = self.orders[oid]
                if to_state in OrderLifecycleState.__members__:
                    # reject invalid reverse transitions into early states from FILLED
                    if (
                        order.state == OrderLifecycleState.FILLED
                        and to_state
                        in (
                            OrderLifecycleState.SUBMIT_PENDING.value,
                            OrderLifecycleState.ORDER_INTENT_CREATED.value,
                            OrderLifecycleState.SIGNAL_RECEIVED.value,
                        )
                    ):
                        journal_issues.append(f"invalid_transition:{oid}:{order.state.value}->{to_state}")
                        self.recovery_required = True
                        self.entry_blocked = True
                        continue
                    order.state = OrderLifecycleState(to_state)
                if row.get("filled_qty") is not None:
                    order.filled_qty = int(row.get("filled_qty") or 0)
                if row.get("quantity") is not None:
                    order.quantity = int(row.get("quantity") or order.quantity)
                if row.get("reservation_id"):
                    order.reservation_id = str(row["reservation_id"])
                if row.get("broker_order_id"):
                    order.broker_order_id = str(row["broker_order_id"])
                if row.get("idempotency_key"):
                    order.idempotency_key = str(row["idempotency_key"])
                    self.by_idempotency[order.idempotency_key] = oid
                if row.get("side"):
                    order.side = str(row["side"])

        # --- capital reservations ---
        if capital_path.is_file():
            for line in capital_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    journal_issues.append("malformed_capital")
                    continue
                _bump_seq(row)
                event = str(row.get("event") or "")
                rid = str(row.get("reservation_id") or "")
                if not rid:
                    continue
                if event == "reserve":
                    if rid in self.ledger.reservations:
                        continue  # no double reservation
                    qty = int(row.get("quantity") or 0)
                    cap = float(row.get("capital_yen") or 0.0)
                    sym = str(row.get("symbol") or "")
                    self.ledger.reserve(
                        symbol=sym, quantity=qty, capital_yen=cap, reservation_id=rid
                    )
                    intent_id = str(row.get("intent_id") or "")
                    if intent_id and intent_id in self.orders:
                        self.orders[intent_id].reservation_id = rid
                elif event == "apply_fill":
                    self.ledger.apply_fill(rid, int(row.get("fill_qty") or row.get("filled_qty") or 0))
                elif event in ("release_remainder", "release_all", "release"):
                    if event == "release_all":
                        self.ledger.release_all(rid)
                    else:
                        self.ledger.release_remainder(rid)

        # --- positions from order fills (BUY +, SELL -) — authoritative after restore ---
        positions: dict[str, int] = {}
        for order in self.orders.values():
            if order.filled_qty <= 0:
                continue
            side = order.side.upper()
            if side in ("BUY", "2", "LONG"):
                positions[order.symbol] = positions.get(order.symbol, 0) + order.filled_qty
            elif side in ("SELL", "1", "SHORT"):
                positions[order.symbol] = positions.get(order.symbol, 0) - order.filled_qty
        self.ledger.open_positions = {k: v for k, v in positions.items() if v > 0}

        # --- kill switch ---
        kill_restored = False
        if kill_path.is_file():
            for line in kill_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                _bump_seq(row)
                ev = str(row.get("event") or "").lower()
                if ev in ("deactivate", "clear"):
                    self.kill_switch = False
                    kill_restored = False
                    continue
                if ev in ("activate", "kill_switch") or row.get("reason"):
                    self.kill_switch = True
                    self.entry_blocked = True
                    reason = str(row.get("reason") or "restored_from_journal")
                    if reason not in self.kill_reasons:
                        self.kill_reasons.append(reason)
                    kill_restored = True

        # Never auto-advance SUBMIT_PENDING / resubmit
        for order in self.orders.values():
            if order.state in (
                OrderLifecycleState.ACKNOWLEDGED,
                OrderLifecycleState.PARTIALLY_FILLED,
                OrderLifecycleState.FILLED,
                OrderLifecycleState.UNKNOWN,
            ):
                # leave as-is; do not transition forward
                pass

        recovery_mode = "NORMAL"
        if self.kill_switch:
            recovery_mode = "KILL_SWITCH_ACTIVE"
        elif self.recovery_required:
            recovery_mode = "JOURNAL_RECOVERY_REQUIRED"
        elif journal_issues:
            recovery_mode = "MANUAL_REVIEW_REQUIRED"
            self.entry_blocked = True

        # sync store sequence cursor
        if max_seq > self.store._seq:
            self.store._seq = max_seq

        open_res = [r for r in self.ledger.reservations.values() if not r.released]
        return {
            "restored_orders": len(self.orders),
            "restored_order_count": len(self.orders),
            "restored_reservation_count": len(open_res),
            "restored_reservations": len(open_res),
            "restored_position_count": sum(1 for v in self.ledger.open_positions.values() if v > 0),
            "restored_positions": dict(self.ledger.open_positions),
            "restored_fill_quantity": sum(o.filled_qty for o in self.orders.values()),
            "restored_remaining_quantity": sum(
                max(0, o.quantity - o.filled_qty)
                for o in self.orders.values()
                if o.state
                in (OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.ACKNOWLEDGED, OrderLifecycleState.SUBMITTED)
            ),
            "journal_sequence_after": max_seq,
            "idempotency_keys": len(self.by_idempotency),
            "kill_switch": self.kill_switch,
            "kill_switch_restored": kill_restored,
            "entry_blocked": self.entry_blocked,
            "recovery_required": self.recovery_required,
            "recovery_mode": recovery_mode,
            "automatic_resubmit_count": automatic_resubmit_count,
            "duplicate_intent_count": duplicate_intent_count,
            "journal_issues": journal_issues,
            "dry_run": True,
            "resubmit": False,
            "broker_write_called": False,
        }


def build_engine(
    *,
    output_dir: Path,
    session_id: str,
    broker: Optional[BrokerAdapter] = None,
    config: Any = None,
) -> LiveOrderSafetyEngine:
    from types import SimpleNamespace

    cfg = config or SimpleNamespace(
        live_trading_enabled=False,
        order_enabled=False,
        dry_run=True,
        max_concurrent_positions=3,
    )
    return LiveOrderSafetyEngine(
        broker=broker or DryRunBrokerAdapter(),
        store=AppendOnlyStore(output_dir=output_dir, session_id=session_id),
        session_id=session_id,
        config=cfg,
    )

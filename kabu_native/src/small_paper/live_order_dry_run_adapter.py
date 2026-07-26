"""
Phase591: Live order dry-run adapter — logs order intents without placing real orders.

Paper Runtime ENTRY/EXIT signals drive state transitions only. No kabusapi sendOrder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

LOT_SIZE = 100
MARGIN_LEVERAGE = 2.0
DEFAULT_ENTRY_TIMEOUT_SEC = 4.0
DEFAULT_ENTRY_LIMIT_OFFSET_TICKS = 0

ORDER_INTENT_FIELDS = (
    "timestamp",
    "event",
    "symbol",
    "side",
    "order_type",
    "margin_type",
    "quantity",
    "limit_price",
    "timeout_sec",
    "reason",
    "linked_paper_trade_id",
    "state_from",
    "state_to",
    "dry_run",
    "trading_enabled",
)

STATE_LOG_FIELDS = (
    "timestamp",
    "symbol",
    "state",
    "event",
    "quantity",
    "filled_quantity",
    "cap_slots_reserved",
    "linked_paper_trade_id",
    "detail",
)

RECONCILE_FIELDS = (
    "timestamp",
    "check",
    "symbol",
    "internal_state",
    "internal_qty",
    "broker_qty",
    "match",
    "action",
    "detail",
)


class OrderState(str, Enum):
    NONE = "NONE"
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    ENTRY_ORDER_PREPARED = "ENTRY_ORDER_PREPARED"
    ENTRY_ORDER_SENT = "ENTRY_ORDER_SENT"
    ENTRY_ORDER_ACCEPTED = "ENTRY_ORDER_ACCEPTED"
    ENTRY_PARTIAL_FILLED = "ENTRY_PARTIAL_FILLED"
    ENTRY_FILLED = "ENTRY_FILLED"
    OPEN_POSITION = "OPEN_POSITION"
    EXIT_SIGNAL = "EXIT_SIGNAL"
    EXIT_ORDER_PREPARED = "EXIT_ORDER_PREPARED"
    EXIT_ORDER_SENT = "EXIT_ORDER_SENT"
    EXIT_ORDER_ACCEPTED = "EXIT_ORDER_ACCEPTED"
    EXIT_PARTIAL_FILLED = "EXIT_PARTIAL_FILLED"
    EXIT_FILLED = "EXIT_FILLED"
    CLOSED = "CLOSED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    SAFE_STOP = "SAFE_STOP"


def dry_run_adapter_enabled(config: Any) -> bool:
    if bool(getattr(config, "live_trading_enabled", False)):
        return False
    return bool(getattr(config, "live_order_dry_run_enabled", True))


def _paper_trade_id(trade: Mapping[str, Any], symbol: str) -> str:
    ent = str(trade.get("entry_time") or "")
    return f"{symbol}:{ent}" if ent else f"{symbol}:{uuid.uuid4().hex[:8]}"


def _limit_entry_price(payload: Mapping[str, Any]) -> Optional[float]:
    """Buy limit at canonical best ask (Sell1). Legacy mode keeps AskPrice label."""
    from small_paper.canonical_board import buy_limit_price

    return buy_limit_price(payload)


def _exit_order_type(exit_reason: str) -> str:
    r = str(exit_reason or "").lower()
    if "hard_stop" in r or "stop" in r or "session" in r:
        return "market"
    if "trailing" in r or "take" in r or "fade" in r:
        return "limit_aggressive"
    return "limit_aggressive"


@dataclass
class SymbolOrderTrack:
    symbol: str
    state: OrderState = OrderState.NONE
    paper_trade_id: str = ""
    quantity: int = LOT_SIZE
    filled_quantity: int = 0
    entry_limit_price: Optional[float] = None
    exit_reason: str = ""
    cap_reserved: bool = False
    transitions: list[tuple[str, OrderState, OrderState]] = field(default_factory=list)


@dataclass
class LiveOrderDryRunSession:
    position_cap: int = 5
    leverage: float = MARGIN_LEVERAGE
    entry_timeout_sec: float = DEFAULT_ENTRY_TIMEOUT_SEC
    tracks: dict[str, SymbolOrderTrack] = field(default_factory=dict)
    cap_slots_reserved: int = 0
    entry_intent_count: int = 0
    exit_intent_count: int = 0
    reconcile_ok_count: int = 0
    reconcile_mismatch_count: int = 0
    safe_stop: bool = False
    safe_stop_reason: str = ""
    new_entry_blocked: bool = False

    def cap_available(self) -> int:
        return max(0, self.position_cap - self.cap_slots_reserved)

    def _reserve_cap(self, track: SymbolOrderTrack) -> bool:
        if track.cap_reserved:
            return True
        if self.cap_slots_reserved >= self.position_cap:
            return False
        track.cap_reserved = True
        self.cap_slots_reserved += 1
        return True

    def _release_cap(self, track: SymbolOrderTrack) -> None:
        if track.cap_reserved:
            track.cap_reserved = False
            self.cap_slots_reserved = max(0, self.cap_slots_reserved - 1)

    def _transition(
        self,
        track: SymbolOrderTrack,
        new_state: OrderState,
        *,
        event: str,
        timestamp: str,
        writer: Any,
        detail: str = "",
    ) -> None:
        old = track.state
        track.state = new_state
        track.transitions.append((timestamp, old, new_state))
        writer.append_live_order_state(
            {
                "timestamp": timestamp,
                "symbol": track.symbol,
                "state": new_state.value,
                "event": event,
                "quantity": track.quantity,
                "filled_quantity": track.filled_quantity,
                "cap_slots_reserved": self.cap_slots_reserved,
                "linked_paper_trade_id": track.paper_trade_id,
                "detail": detail,
            }
        )

    def _emit_intent(
        self,
        *,
        timestamp: str,
        event: str,
        track: SymbolOrderTrack,
        side: str,
        order_type: str,
        limit_price: Optional[float],
        timeout_sec: Optional[float],
        reason: str,
        state_from: OrderState,
        state_to: OrderState,
        writer: Any,
        config: Any,
    ) -> None:
        writer.append_live_order_intent(
            {
                "timestamp": timestamp,
                "event": event,
                "symbol": track.symbol,
                "side": side,
                "order_type": order_type,
                "margin_type": "credit_new",
                "quantity": track.quantity,
                "limit_price": limit_price,
                "timeout_sec": timeout_sec,
                "reason": reason,
                "linked_paper_trade_id": track.paper_trade_id,
                "state_from": state_from.value,
                "state_to": state_to.value,
                "dry_run": True,
                "trading_enabled": bool(getattr(config, "live_trading_enabled", False)),
            }
        )


def on_paper_entry_accepted(
    session: LiveOrderDryRunSession,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    timestamp: str,
    writer: Any,
    config: Any,
) -> Optional[dict[str, Any]]:
    if session.safe_stop or session.new_entry_blocked:
        return {"blocked": True, "reason": session.safe_stop_reason or "new_entry_blocked"}
    if symbol in session.tracks and session.tracks[symbol].state not in (
        OrderState.NONE,
        OrderState.CLOSED,
        OrderState.CANCELLED,
    ):
        return {"blocked": True, "reason": "duplicate_symbol_open_or_pending"}

    track = SymbolOrderTrack(
        symbol=symbol,
        state=OrderState.NONE,
        paper_trade_id=_paper_trade_id(trade, symbol),
        quantity=LOT_SIZE,
    )
    session.tracks[symbol] = track

    session._transition(track, OrderState.ENTRY_SIGNAL, event="paper_entry_accepted", timestamp=timestamp, writer=writer)
    if not session._reserve_cap(track):
        session._transition(
            track,
            OrderState.ERROR,
            event="cap_exceeded",
            timestamp=timestamp,
            writer=writer,
            detail=f"cap={session.position_cap}",
        )
        return {"blocked": True, "reason": "position_cap_exceeded"}

    limit_px = _limit_entry_price(payload)
    track.entry_limit_price = limit_px
    session._transition(
        track, OrderState.ENTRY_ORDER_PREPARED, event="prepare_entry_limit", timestamp=timestamp, writer=writer
    )
    session._emit_intent(
        timestamp=timestamp,
        event="entry_order_prepare",
        track=track,
        side="buy",
        order_type="limit",
        limit_price=limit_px,
        timeout_sec=session.entry_timeout_sec,
        reason="paper_entry_signal",
        state_from=OrderState.ENTRY_ORDER_PREPARED,
        state_to=OrderState.ENTRY_ORDER_SENT,
        writer=writer,
        config=config,
    )
    session._transition(
        track, OrderState.ENTRY_ORDER_SENT, event="dry_run_entry_sent", timestamp=timestamp, writer=writer
    )
    session._transition(
        track,
        OrderState.ENTRY_ORDER_ACCEPTED,
        event="dry_run_entry_accepted",
        timestamp=timestamp,
        writer=writer,
    )
    track.filled_quantity = track.quantity
    session._transition(
        track, OrderState.ENTRY_FILLED, event="dry_run_entry_filled", timestamp=timestamp, writer=writer
    )
    session._transition(
        track, OrderState.OPEN_POSITION, event="position_open", timestamp=timestamp, writer=writer
    )
    session.entry_intent_count += 1
    return {"ok": True, "paper_trade_id": track.paper_trade_id, "limit_price": limit_px}


def on_paper_exit_signal(
    session: LiveOrderDryRunSession,
    *,
    symbol: str,
    context: Mapping[str, Any],
    timestamp: str,
    writer: Any,
    config: Any,
) -> Optional[dict[str, Any]]:
    track = session.tracks.get(symbol)
    if track is None or track.state != OrderState.OPEN_POSITION:
        return None

    exit_reason = str(context.get("exit_reason") or context.get("reason") or "structural_exit")
    track.exit_reason = exit_reason
    order_type = _exit_order_type(exit_reason)

    session._transition(track, OrderState.EXIT_SIGNAL, event="paper_exit_signal", timestamp=timestamp, writer=writer)
    session._transition(
        track, OrderState.EXIT_ORDER_PREPARED, event="prepare_exit", timestamp=timestamp, writer=writer
    )
    try:
        exit_px = float(context.get("exit_price") or context.get("current_price") or 0)
    except (TypeError, ValueError):
        exit_px = 0.0
    limit_px = round(exit_px, 1) if exit_px > 0 and order_type != "market" else None
    session._emit_intent(
        timestamp=timestamp,
        event="exit_order_prepare",
        track=track,
        side="sell",
        order_type=order_type,
        limit_price=limit_px,
        timeout_sec=None,
        reason=exit_reason,
        state_from=OrderState.EXIT_ORDER_PREPARED,
        state_to=OrderState.EXIT_ORDER_SENT,
        writer=writer,
        config=config,
    )
    session._transition(
        track, OrderState.EXIT_ORDER_SENT, event="dry_run_exit_sent", timestamp=timestamp, writer=writer
    )
    session._transition(
        track,
        OrderState.EXIT_ORDER_ACCEPTED,
        event="dry_run_exit_accepted",
        timestamp=timestamp,
        writer=writer,
    )
    track.filled_quantity = 0
    session._transition(
        track, OrderState.EXIT_FILLED, event="dry_run_exit_filled", timestamp=timestamp, writer=writer
    )
    session._transition(track, OrderState.CLOSED, event="position_closed", timestamp=timestamp, writer=writer)
    session._release_cap(track)
    session.exit_intent_count += 1
    return {"ok": True, "exit_reason": exit_reason, "order_type": order_type}


def reconcile_session_positions(
    session: LiveOrderDryRunSession,
    *,
    timestamp: str,
    writer: Any,
    open_symbols: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Dry-run reconcile: internal OPEN vs expected paper observer set (broker_qty=simulated)."""
    rows: list[dict[str, Any]] = []
    expected_open = open_symbols or set()
    internal_open = {
        sym
        for sym, tr in session.tracks.items()
        if tr.state in (OrderState.OPEN_POSITION, OrderState.ENTRY_PARTIAL_FILLED, OrderState.ENTRY_FILLED)
    }
    all_syms = internal_open | expected_open
    for sym in sorted(all_syms):
        tr = session.tracks.get(sym)
        internal_state = tr.state.value if tr else OrderState.NONE.value
        internal_qty = tr.filled_quantity if tr and tr.state == OrderState.OPEN_POSITION else 0
        broker_qty = LOT_SIZE if sym in expected_open else 0
        match = (sym in internal_open) == (sym in expected_open) and internal_qty == broker_qty
        action = "ok" if match else "safe_stop_recommended"
        if not match:
            session.reconcile_mismatch_count += 1
            session.safe_stop = True
            session.safe_stop_reason = session.safe_stop_reason or f"reconcile_mismatch:{sym}"
            session.new_entry_blocked = True
        else:
            session.reconcile_ok_count += 1
        row = {
            "timestamp": timestamp,
            "check": "session_end_reconcile",
            "symbol": sym,
            "internal_state": internal_state,
            "internal_qty": internal_qty,
            "broker_qty": broker_qty,
            "match": match,
            "action": action,
            "detail": "dry_run_simulated_broker",
        }
        rows.append(row)
        writer.append_live_position_reconcile(row)
    return rows


def dry_run_summary_fields(session: Optional[LiveOrderDryRunSession]) -> dict[str, Any]:
    if session is None:
        return {"live_order_dry_run_enabled": False}
    if session.entry_intent_count <= 0 and session.exit_intent_count <= 0:
        return {
            "live_order_dry_run_enabled": True,
            "live_order_dry_run_entry_intents": 0,
            "live_order_dry_run_exit_intents": 0,
            "live_order_dry_run_cap_reserved_end": session.cap_slots_reserved,
            "live_order_dry_run_safe_stop": session.safe_stop,
            "live_trading_enabled": False,
        }
    return {
        "live_order_dry_run_enabled": True,
        "live_order_dry_run_entry_intents": session.entry_intent_count,
        "live_order_dry_run_exit_intents": session.exit_intent_count,
        "live_order_dry_run_cap_reserved_end": session.cap_slots_reserved,
        "live_order_dry_run_reconcile_ok": session.reconcile_ok_count,
        "live_order_dry_run_reconcile_mismatch": session.reconcile_mismatch_count,
        "live_order_dry_run_safe_stop": session.safe_stop,
        "live_order_dry_run_safe_stop_reason": session.safe_stop_reason or None,
        "live_trading_enabled": False,
        "live_order_margin_leverage": session.leverage,
        "live_order_lot_size": LOT_SIZE,
    }

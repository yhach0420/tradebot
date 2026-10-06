"""In-memory broker. No network. No real order API."""

from __future__ import annotations

from dataclasses import dataclass, field

from small_paper.x1_live_readiness.state_machine import BrokerTimeout, BrokerUnreadable, BrokerView


@dataclass
class BrokerResult:
    status: str
    broker_order_id: str = ""


@dataclass
class MockBroker:
    timeout: bool = False
    reject: bool = False
    ack_lost: bool = False
    unreadable: bool = False
    orders: dict[str, dict] = field(default_factory=dict)
    submit_calls: int = 0
    cancel_calls: int = 0
    _seq: int = 0

    def submit(self, intent) -> BrokerResult:
        self.submit_calls += 1
        if self.reject:
            return BrokerResult(status="REJECTED")
        self._seq += 1
        broker_order_id = f"B{self._seq}"
        self.orders[broker_order_id] = {
            "order_id": broker_order_id,
            "order_intent_id": intent.order_intent_id,
            "strategy_trade_id": intent.strategy_trade_id,
            "signal_uid": intent.signal_uid,
            "side": intent.side,
            "symbol": intent.symbol,
            "intended_qty": intent.intended_qty,
            "cum_qty": 0,
            "status": "WORKING",
            "execs": [],
        }
        if self.timeout:
            raise BrokerTimeout("submit timeout")
        if self.ack_lost:
            return BrokerResult(status="ACK_LOST", broker_order_id=broker_order_id)
        return BrokerResult(status="ACKNOWLEDGED", broker_order_id=broker_order_id)

    def request_cancel(self, intent) -> None:
        self.cancel_calls += 1
        oid = intent.broker_order_id
        if oid and oid in self.orders and self.orders[oid]["status"] == "WORKING":
            self.orders[oid]["status"] = "CANCEL_REQUESTED"

    def fill(self, broker_order_id: str, exec_id: str, qty: int) -> None:
        order = self.orders[broker_order_id]
        if any(item["exec_id"] == exec_id for item in order["execs"]):
            return
        order["execs"].append({"exec_id": exec_id, "qty": int(qty)})
        order["cum_qty"] = int(order["cum_qty"]) + int(qty)
        if order["cum_qty"] >= order["intended_qty"]:
            order["status"] = "FILLED"

    def force_position(self, symbol: str, qty: int) -> None:
        """Test fixture override. View uses this instead of order sums when set."""
        self._forced = getattr(self, "_forced", {})
        self._forced[symbol] = int(qty)

    def view(self) -> BrokerView:  # type: ignore[no-redef]
        if self.unreadable:
            raise BrokerUnreadable("BROKER_UNREADABLE")
        positions: dict[str, int] = {}
        for order in self.orders.values():
            sign = 1 if order["side"] == "BUY" else -1
            positions[order["symbol"]] = positions.get(order["symbol"], 0) + sign * int(order["cum_qty"])
        forced = getattr(self, "_forced", {})
        positions.update(forced)
        return BrokerView(
            readable=True,
            positions={k: v for k, v in positions.items() if v != 0},
            orders=[
                {k: v for k, v in order.items() if k != "execs"}
                for order in self.orders.values()
                if order["status"] != "REJECTED"
            ],
        )

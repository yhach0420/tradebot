"""Paper Runtime Local Market Bus consumer (MARKET_INGRESS_V2).

Paper does NOT own WebSocket. Receives envelopes after Raw persist.
ACK is sent only after successful processing — never on receive.
"""
from __future__ import annotations

import asyncio
import queue
from typing import Any, AsyncIterator, Callable, Optional

from small_paper.local_market_bus import LocalMarketBusConsumer, bus_host, bus_port
from small_paper.market_ingress_protocol import (
    KIND_ENTRY_BLOCK,
    KIND_ENTRY_UNBLOCK,
    KIND_GAP,
    KIND_MARKET_PUSH,
    MarketEnvelope,
    kabu_payload_from_envelope,
    now_iso,
)
from small_paper.ws_freeze_recovery import make_recv_timeout_tick


class PaperMarketBusBridge:
    """Thread→asyncio bridge yielding Kabu-shaped payloads for pilot_runner."""

    def __init__(
        self,
        *,
        consumer_id: str = "paper_runtime",
        host: Optional[str] = None,
        port: Optional[int] = None,
        ingress_session_id: str = "",
        on_control: Optional[Callable[[MarketEnvelope], None]] = None,
    ) -> None:
        self.consumer_id = consumer_id
        self.q: queue.Queue[Any] = queue.Queue(maxsize=50000)
        self.on_control = on_control
        self.consumer = LocalMarketBusConsumer(
            consumer_id=consumer_id,
            host=host or bus_host(),
            port=int(port or bus_port()),
            on_envelope=self._on_envelope,
            ingress_session_id=ingress_session_id,
        )
        self.entry_blocked = False
        self.entry_block_reason = ""
        self.last_sequence = 0
        self.last_event_at = ""
        self.last_ack_sequence = 0
        self.gaps = 0
        self.process_errors = 0
        self._ack_halted = False
        self.started = False

    def _on_envelope(self, env: MarketEnvelope) -> None:
        # Receive-only path: never ACK here.
        if env.kind == KIND_ENTRY_BLOCK:
            self.entry_blocked = True
            self.entry_block_reason = env.entry_block_reason or "ingress_entry_block"
            if self.on_control:
                self.on_control(env)
            return
        if env.kind == KIND_ENTRY_UNBLOCK:
            self.entry_blocked = False
            self.entry_block_reason = ""
            if self.on_control:
                self.on_control(env)
            return
        if env.kind == KIND_GAP:
            self.gaps += 1
            if self.on_control:
                self.on_control(env)
            return
        if env.kind != KIND_MARKET_PUSH:
            return
        self.last_sequence = int(env.sequence)
        self.last_event_at = env.event_time or env.received_at
        if env.ingress_session_id:
            self.consumer.ingress_session_id = env.ingress_session_id
        payload = kabu_payload_from_envelope(env)
        payload = dict(payload)
        payload["__ingress_session_id__"] = env.ingress_session_id
        payload["__ingress_sequence__"] = env.sequence
        payload["__persisted_at__"] = env.persisted_at
        try:
            self.q.put_nowait(payload)
        except queue.Full:
            try:
                self.q.get_nowait()
            except Exception:
                pass
            try:
                self.q.put_nowait(payload)
            except Exception:
                pass
            self.gaps += 1

    def start(self) -> bool:
        ok = self.consumer.connect()
        # Resume contiguous ACK from publisher hint
        self.last_ack_sequence = max(self.last_ack_sequence, int(self.consumer.last_ack_sequence or 0))
        self.clear_ack_halt()
        self.consumer.start()
        self.started = True
        return ok

    def stop(self) -> None:
        self.consumer.stop()
        self.started = False

    def ack_processed(self, payload: Any) -> bool:
        """Call only after pilot_runner processing success."""
        if self._ack_halted:
            return False
        if not isinstance(payload, dict):
            return False
        seq = payload.get("__ingress_sequence__")
        if seq is None:
            return False
        # Contiguous ACK only — never skip a failed sequence.
        expected = int(self.last_ack_sequence) + 1
        if int(seq) != expected:
            self.mark_process_error("ack_gap_or_skip")
            return False
        sess = str(payload.get("__ingress_session_id__") or self.consumer.ingress_session_id)
        ok = self.consumer.send_ack(int(seq), ingress_session_id=sess, processed_at=now_iso())
        if ok:
            self.last_ack_sequence = max(self.last_ack_sequence, int(seq))
        else:
            self.process_errors += 1
            self.entry_blocked = True
            self.entry_block_reason = "consumer_ack_failed"
            self._ack_halted = True
        return ok

    def mark_process_error(self, reason: str = "consumer_process_error") -> None:
        self.process_errors += 1
        self.entry_blocked = True
        self.entry_block_reason = reason
        self._ack_halted = True
        # Do not ACK — last_ack stays put.

    def clear_ack_halt(self) -> None:
        """Allow ACK resume after consumer recovery/reconnect."""
        self._ack_halted = False
        self.entry_blocked = False
        self.entry_block_reason = ""

    def health(self) -> dict[str, Any]:
        h = self.consumer.health()
        h.update(
            {
                "entry_blocked": self.entry_blocked,
                "entry_block_reason": self.entry_block_reason,
                "gaps": self.gaps,
                "queue_depth": self.q.qsize(),
                "ingress_connected": self.consumer.connected,
                "paper_consumer_ready": bool(self.consumer.ready and self.consumer.connected),
                "paper_consumer_transport": self.consumer.transport,
                "last_ack_sequence": self.last_ack_sequence or self.consumer.last_ack_sequence,
                "process_errors": self.process_errors,
                "market_source": "LOCAL_MARKET_BUS",
            }
        )
        return h

    async def iter_messages(self, *, recv_poll_sec: float = 5.0) -> AsyncIterator[dict[str, Any]]:
        consecutive = 0
        while True:
            try:
                item = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.q.get(timeout=max(0.1, float(recv_poll_sec)))
                )
                consecutive = 0
                yield item
            except Exception:
                consecutive += 1
                yield make_recv_timeout_tick(consecutive)

    def process_queue_item(
        self,
        payload: dict[str, Any],
        *,
        handler: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> bool:
        """Sync helper for preflight: validate → process → ACK."""
        try:
            if handler is not None:
                handler(payload)
            return self.ack_processed(payload)
        except Exception:
            self.mark_process_error("consumer_process_error")
            return False

"""Paper Runtime Local Market Bus consumer (MARKET_INGRESS_V2).

Paper does NOT own WebSocket. Receives envelopes after Raw persist.
ACK is sent only after successful processing — never on receive.
"""
from __future__ import annotations

import asyncio
import queue
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from small_paper.consumer_ack_state import write_ack_checkpoint
from small_paper.consumer_lag_policy import (
    DEFAULT_WARMUP_LOOKBACK_EVENTS,
    LagPolicyInput,
    REASON_REALTIME_RESYNC,
    evaluate_lag_policy,
    read_ingress_status,
)
from small_paper.local_market_bus import (
    RESUME_MODE_CONTINUE,
    LocalMarketBusConsumer,
    bus_host,
    bus_port,
)
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
        resume_mode: str = RESUME_MODE_CONTINUE,
        resume_from_ack: int = 0,
        native_root: Optional[Path] = None,
        trading_date: str = "",
    ) -> None:
        self.consumer_id = consumer_id
        # Deep buffer: continuous PUSH can exceed 50k before Paper finishes warmup.
        self.q: queue.Queue[Any] = queue.Queue(maxsize=200000)
        self.on_control = on_control
        self.native_root = Path(native_root) if native_root else None
        self.trading_date = str(trading_date or "")
        self.consumer = LocalMarketBusConsumer(
            consumer_id=consumer_id,
            host=host or bus_host(),
            port=int(port or bus_port()),
            on_envelope=self._on_envelope,
            ingress_session_id=ingress_session_id,
            resume_mode=resume_mode,
            resume_from_ack=int(resume_from_ack or 0),
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
        self.warmup_only = False
        self.resync_audit: dict[str, Any] = {}
        self._ack_persist_every = 250
        self._acks_since_persist = 0
        self.lag_policy_state = ""

    def _on_envelope(self, env: MarketEnvelope) -> None:
        # Receive-only path: never ACK here.
        if env.kind == KIND_ENTRY_BLOCK:
            self.entry_blocked = True
            self.entry_block_reason = env.entry_block_reason or "ingress_entry_block"
            if self.on_control:
                self.on_control(env)
            return
        if env.kind == KIND_ENTRY_UNBLOCK:
            # Keep local warmup block until warmup cleared.
            if not self.warmup_only:
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
        # Causal Capture/ingress clock — NOT consumer wall time.
        # Frozen load_board_events board.t = recorded_at; live must use the same axis.
        if env.received_at:
            payload["__ingress_received_at__"] = env.received_at
            payload.setdefault("received_at", env.received_at)
            payload.setdefault("recorded_at", env.received_at)
        if env.event_time:
            payload["__ingress_event_time__"] = env.event_time
            payload.setdefault("event_time", env.event_time)
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

    def _persist_ack(self, *, publisher_hint: int = 0, reason: str = "") -> None:
        if self.native_root is None or not self.trading_date:
            return
        try:
            write_ack_checkpoint(
                self.native_root,
                ingress_session_id=str(self.consumer.ingress_session_id or ""),
                trading_date=self.trading_date,
                last_ack_sequence=int(self.last_ack_sequence),
                publisher_last_sequence=int(publisher_hint or 0),
                reason=reason,
                consumer_id=self.consumer_id,
            )
        except Exception:
            pass

    def ack_processed(self, payload: Any) -> bool:
        """Call only after pilot_runner processing success."""
        if self._ack_halted:
            return False
        if not isinstance(payload, dict):
            return False
        seq = payload.get("__ingress_sequence__")
        if seq is None:
            return False
        seq_i = int(seq)
        expected = int(self.last_ack_sequence) + 1
        if seq_i < expected:
            return False
        if seq_i > expected:
            # Queue-drop / reconnect gap: keep ENTRY blocked, but resync ACK cursor so
            # lag can recover instead of permanently halting at the gap (ack_halted).
            self.gaps += 1
            self.process_errors += 1
            self.entry_blocked = True
            self.entry_block_reason = "ack_gap_resync"
            self.last_ack_sequence = seq_i - 1
        sess = str(payload.get("__ingress_session_id__") or self.consumer.ingress_session_id)
        ok = self.consumer.send_ack(seq_i, ingress_session_id=sess, processed_at=now_iso())
        if ok:
            self.last_ack_sequence = max(self.last_ack_sequence, seq_i)
            self._acks_since_persist += 1
            if self._acks_since_persist >= self._ack_persist_every:
                self._acks_since_persist = 0
                self._persist_ack(reason="periodic")
            # Gap resync keeps ENTRY blocked until Ingress promotes on lag==0 + first-push rules.
            if self.entry_block_reason == "ack_gap_resync":
                pass
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
        if not self.warmup_only:
            self.entry_blocked = False
            self.entry_block_reason = ""

    def drain_queue(self, *, max_items: int = 500000) -> int:
        n = 0
        while n < max_items:
            try:
                self.q.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def realtime_resync_to_publisher_head(
        self,
        *,
        publisher_last_sequence: int,
        open_positions: int = 0,
        skipped_from: Optional[int] = None,
        warmup_lookback: int = DEFAULT_WARMUP_LOOKBACK_EVENTS,
    ) -> dict[str, Any]:
        """OPEN=0 path: ACK jump to publisher head; drain backlog; enter warmup gate.

        Works against Ingress that accepts any ACK <= publisher head (no code reload required).
        """
        if int(open_positions) > 0:
            return {
                "ok": False,
                "reason": "OPEN_POSITIONS_BLOCK_RESYNC",
                "open_positions": int(open_positions),
            }
        head = int(publisher_last_sequence)
        if head <= 0:
            return {"ok": False, "reason": "invalid_publisher_head"}
        from_seq = int(skipped_from if skipped_from is not None else self.last_ack_sequence)
        if from_seq < 0:
            from_seq = 0
        self.warmup_only = True
        self.entry_blocked = True
        self.entry_block_reason = REASON_REALTIME_RESYNC
        self.clear_ack_halt()
        self._ack_halted = False
        sess = str(self.consumer.ingress_session_id or "")
        ok = self.consumer.send_ack(head, ingress_session_id=sess, processed_at=now_iso())
        if ok:
            self.last_ack_sequence = head
            self.consumer.last_ack_sequence = head
        drained = self.drain_queue()
        warmup_from = max(0, head - int(warmup_lookback))
        audit = {
            "ok": bool(ok),
            "reason": REASON_REALTIME_RESYNC,
            "skipped_from_sequence": from_seq,
            "skipped_to_sequence": head,
            "skipped_count": max(0, head - from_seq),
            "warmup_from": warmup_from,
            "warmup_to": head,
            "resync_at": now_iso(),
            "drained_queue_items": drained,
            "ingress_session_id": sess,
        }
        self.resync_audit = audit
        self._persist_ack(publisher_hint=head, reason=REASON_REALTIME_RESYNC)
        return audit

    def finish_warmup(self) -> None:
        self.warmup_only = False
        self.entry_blocked = False
        self.entry_block_reason = ""

    def maybe_apply_lag_policy(
        self,
        *,
        open_positions: int = 0,
        publisher_rate: float = 0.0,
        consumer_rate: float = 0.0,
        ack_rate: float = 0.0,
    ) -> dict[str, Any]:
        status: dict[str, Any] = {}
        if self.native_root is not None and self.trading_date:
            status = read_ingress_status(self.native_root, self.trading_date)
        pub = int(status.get("publisher_last_sequence") or self.consumer.publisher_last_sequence_hint or 0)
        ack = int(self.last_ack_sequence or status.get("paper_consumer_last_ack") or 0)
        lag = max(0, pub - ack) if pub else int(status.get("paper_consumer_lag") or 0)
        decision = evaluate_lag_policy(
            LagPolicyInput(
                lag=lag,
                publisher_rate=publisher_rate,
                consumer_rate=consumer_rate,
                ack_rate=ack_rate,
                open_positions=open_positions,
                queue_depth=self.q.qsize(),
            )
        )
        self.lag_policy_state = decision.state
        out: dict[str, Any] = {
            "decision": decision.state,
            "reason": decision.reason,
            "lag": lag,
            "pub": pub,
            "ack": ack,
        }
        if decision.allow_realtime_resync and decision.allow_skip_backlog and pub > 0:
            out["resync"] = self.realtime_resync_to_publisher_head(
                publisher_last_sequence=pub,
                open_positions=open_positions,
                skipped_from=ack,
            )
        elif decision.entry_block:
            self.entry_blocked = True
            self.entry_block_reason = decision.reason or self.entry_block_reason or "CONSUMER_LAG"
        return out

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
                "warmup_only": self.warmup_only,
                "lag_policy_state": self.lag_policy_state,
                "resync_audit": self.resync_audit,
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


def force_paper_realtime_resync_from_status(
    bridge: PaperMarketBusBridge,
    *,
    native_root: Path,
    trading_date: str,
    open_positions: int = 0,
) -> dict[str, Any]:
    """Convenience for PM recovery scripts."""
    st = read_ingress_status(native_root, trading_date)
    pub = int(st.get("publisher_last_sequence") or 0)
    ack = int(bridge.last_ack_sequence or st.get("paper_consumer_last_ack") or 0)
    return bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=pub,
        open_positions=open_positions,
        skipped_from=ack,
    )

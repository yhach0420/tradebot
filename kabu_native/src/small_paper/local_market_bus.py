"""Local Market Bus — Ingress publisher → Paper/Observer consumers + ACK.

P0-2C thread model:
- Raw Writer is NOT a consumer (upstream of publish).
- publish() never sendall: ring offer + notify only (WS/Capture must not wait).
- Each TCP client thread pulls ring or persisted Capture JSONL (disk-backed catch-up).
- Slow client sendall is isolated to that client thread; lock is not held during IO.
- Ring is a bounded hot cache (default 20000), not delivery SoT. Eviction is not a drop.
- lag = publisher_last_sequence - last_ack_sequence
- Gaps are explicit events — never silent skip.
- ACK only after successful consumer processing (not on receive).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Optional

from small_paper.capture_sequence_reader import AbortCheck, CaptureSequenceReader
from small_paper.market_ingress_protocol import (
    DEFAULT_BUS_HOST,
    DEFAULT_BUS_PORT,
    KIND_GAP,
    KIND_MARKET_PUSH,
    MarketEnvelope,
    now_iso,
)

ENV_BUS_PORT = "TRADEBOT_MARKET_BUS_PORT"
ENV_BUS_HOST = "TRADEBOT_MARKET_BUS_HOST"
DEFAULT_LAG_ENTRY_BLOCK = 5000
DEFAULT_RING = 20000
# CONTINUE disk lookup must yield so a REALTIME resync ACK can be read.
DISK_LOOKUP_CHUNK = 256
FANOUT_SOURCE_RING = "ring"
FANOUT_SOURCE_DISK = "disk"
FANOUT_SOURCE_FAIL_CLOSE = "realtime_fail_close"

MSG_SUBSCRIBE = "subscribe"
MSG_ACK = "ack"
MSG_READY = "ready"
RESUME_MODE_CONTINUE = "continue"
RESUME_MODE_REALTIME = "realtime"


def bus_host(*, environ: Optional[dict[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_BUS_HOST) or DEFAULT_BUS_HOST)


def bus_port(*, environ: Optional[dict[str, str]] = None) -> int:
    env = environ if environ is not None else os.environ
    try:
        return int(env.get(ENV_BUS_PORT) or DEFAULT_BUS_PORT)
    except Exception:
        return DEFAULT_BUS_PORT


@dataclass
class ConsumerState:
    consumer_id: str
    last_ack_sequence: int = 0
    last_delivered_sequence: int = 0
    last_ack_at: str = ""
    lag: int = 0
    connected: bool = False
    ready: bool = False
    transport: str = "unknown"  # inproc | TCP
    errors: int = 0
    last_error: str = ""
    ingress_session_id: str = ""
    # Atomic REALTIME_RESYNC commit (ACK + fanout + Paper watermark).
    resync_generation: int = 0
    resync_head_seq: int = 0
    resync_head_event_time: str = ""
    fanout_last_ack: int = 0
    fanout_last_market: int = 0
    fanout_last_tick: int = 0
    fanout_source: str = ""
    physical_reader_invalidated: bool = False
    physical_reader_generation: int = 0
    physical_reader_sequence: int = 0
    first_post_resync_seq: int = 0
    reader_abort_count: int = 0
    resync_commit_mono: float = 0.0
    first_post_resync_mono: float = 0.0
    ring_handoff_reason: str = ""


@dataclass
class AckResult:
    ok: bool
    reason: str = ""
    last_ack_sequence: int = 0
    lag: int = 0
    resync_generation: int = 0
    resync_head_seq: int = 0
    resync_head_event_time: str = ""


@dataclass
class LocalMarketBusPublisher:
    """In-process ring + TCP fanout with bidirectional ACK channel."""

    host: str = DEFAULT_BUS_HOST
    port: int = DEFAULT_BUS_PORT
    ring_size: int = DEFAULT_RING
    lag_entry_block: int = DEFAULT_LAG_ENTRY_BLOCK
    enable_tcp: bool = True
    ingress_session_id: str = ""

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _cond: Optional[threading.Condition] = field(default=None, init=False)
    _seq_published: int = field(default=0, init=False)
    _tick: int = field(default=0, init=False)
    _ring_start_tick: int = field(default=1, init=False)
    _ring: Deque[tuple[int, MarketEnvelope]] = field(default_factory=deque, init=False)
    _market_by_seq: dict[int, MarketEnvelope] = field(default_factory=dict, init=False)
    _consumers: dict[str, ConsumerState] = field(default_factory=dict, init=False)
    _handlers: dict[str, Callable[[MarketEnvelope], None]] = field(default_factory=dict, init=False)
    _tcp_clients: list[socket.socket] = field(default_factory=list, init=False)
    _tcp_by_consumer: dict[str, socket.socket] = field(default_factory=dict, init=False)
    _server: Optional[socket.socket] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _inproc_thread: Optional[threading.Thread] = field(default=None, init=False)
    _readers: list[threading.Thread] = field(default_factory=list, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _capture_dir: Optional[Path] = field(default=None, init=False)
    publish_ok: int = 0
    publish_fail: int = 0
    gap_count: int = 0
    ack_reject_count: int = 0
    ring_evict_count: int = 0
    disk_catchup_reads: int = 0
    disk_stale_resync_aborts: int = 0
    stale_disk_reads_after_resync: int = 0
    listening: bool = False
    on_ack: Optional[Callable[[str, AckResult], None]] = None

    def __post_init__(self) -> None:
        self._cond = threading.Condition(self._lock)

    def attach_capture_dir(self, path: Path | str | None) -> None:
        """Persist directory is delivery SoT when the ring cache has evicted."""
        self._capture_dir = Path(path) if path else None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self._cond is None:
            self._cond = threading.Condition(self._lock)
        self._inproc_thread = threading.Thread(target=self._inproc_loop, name="market-bus-inproc", daemon=True)
        self._inproc_thread.start()
        if self.enable_tcp:
            self._thread = threading.Thread(target=self._serve, name="market-bus-pub", daemon=True)
            self._thread.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not self.listening:
                time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()
        cond = self._cond
        if cond is not None:
            with cond:
                cond.notify_all()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        with self._lock:
            for c in list(self._tcp_clients):
                try:
                    c.close()
                except Exception:
                    pass
            self._tcp_clients.clear()
            self._tcp_by_consumer.clear()
        self.listening = False
        me = threading.current_thread()
        if self._thread is not None and self._thread.is_alive() and self._thread is not me:
            self._thread.join(timeout=2.0)
        if self._inproc_thread is not None and self._inproc_thread.is_alive() and self._inproc_thread is not me:
            self._inproc_thread.join(timeout=2.0)
        for t in list(self._readers):
            if t.is_alive() and t is not me:
                t.join(timeout=1.0)
        self._readers = [t for t in self._readers if t.is_alive()]
        self._thread = None
        self._inproc_thread = None

    def subscribe(
        self,
        consumer_id: str,
        handler: Optional[Callable[[MarketEnvelope], None]] = None,
        *,
        transport: str = "inproc",
    ) -> None:
        with self._lock:
            st = self._consumers.get(consumer_id) or ConsumerState(consumer_id=consumer_id)
            st.connected = True
            st.ready = True
            st.transport = transport
            st.ingress_session_id = self.ingress_session_id
            self._consumers[consumer_id] = st
            if handler is not None:
                self._handlers[consumer_id] = handler

    def unsubscribe(self, consumer_id: str) -> None:
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st:
                st.connected = False
                st.ready = False
            self._handlers.pop(consumer_id, None)
            sock = self._tcp_by_consumer.pop(consumer_id, None)
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
                if sock in self._tcp_clients:
                    self._tcp_clients.remove(sock)

    def ack(
        self,
        consumer_id: str,
        sequence: int,
        *,
        ingress_session_id: str = "",
        processed_at: str = "",
        resync_commit: bool = False,
        resync_head_event_time: str = "",
        resync_generation: int = 0,
    ) -> AckResult:
        """Single ACK path for inproc + TCP. Rejects unknown/stale/future/reverse.

        A REALTIME_RESYNC commit is an atomic watermark: last_ack, fanout cursor,
        and resync generation all move to the same publisher-head sequence.
        CONTINUE-mode gap ACKs must not set resync_commit (fanout stays sequential).
        """
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                self.ack_reject_count += 1
                return AckResult(ok=False, reason="unknown_consumer")
            sess = str(ingress_session_id or "")
            if sess and self.ingress_session_id and sess != self.ingress_session_id:
                self.ack_reject_count += 1
                st.errors += 1
                st.last_error = "unknown_session_ack"
                return AckResult(
                    ok=False,
                    reason="unknown_session",
                    last_ack_sequence=st.last_ack_sequence,
                    lag=st.lag,
                )
            seq = int(sequence)
            if seq < int(st.last_ack_sequence):
                self.ack_reject_count += 1
                st.errors += 1
                st.last_error = "ack_sequence_regression"
                return AckResult(
                    ok=False,
                    reason="sequence_regression",
                    last_ack_sequence=st.last_ack_sequence,
                    lag=st.lag,
                )
            if seq > int(self._seq_published):
                self.ack_reject_count += 1
                st.errors += 1
                st.last_error = "ack_beyond_publisher"
                return AckResult(
                    ok=False,
                    reason="beyond_publisher",
                    last_ack_sequence=st.last_ack_sequence,
                    lag=st.lag,
                )
            st.last_ack_sequence = seq
            st.last_ack_at = processed_at or now_iso()
            st.lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))
            if resync_commit:
                gen = int(resync_generation or 0)
                if gen <= 0:
                    gen = int(st.resync_generation) + 1
                st.resync_generation = max(int(st.resync_generation), gen)
                st.resync_head_seq = seq
                st.resync_head_event_time = str(resync_head_event_time or "")
                st.last_delivered_sequence = max(int(st.last_delivered_sequence), seq)
                st.fanout_last_ack = seq
                st.fanout_last_market = seq
                tick, source, reason = self._ring_handoff_locked(seq)
                st.fanout_last_tick = int(tick)
                st.fanout_source = source
                st.ring_handoff_reason = reason
                st.physical_reader_invalidated = True
                st.physical_reader_generation = int(st.resync_generation)
                st.physical_reader_sequence = 0
                st.first_post_resync_seq = 0
                st.first_post_resync_mono = 0.0
                st.resync_commit_mono = time.monotonic()
                st.reader_abort_count = int(st.reader_abort_count) + 1
            result = AckResult(
                ok=True,
                reason="ok" if not resync_commit else "realtime_resync_commit",
                last_ack_sequence=st.last_ack_sequence,
                lag=st.lag,
                resync_generation=int(st.resync_generation),
                resync_head_seq=int(st.resync_head_seq),
                resync_head_event_time=str(st.resync_head_event_time or ""),
            )
            if resync_commit and self._cond is not None:
                self._cond.notify_all()
        if self.on_ack is not None and result.ok:
            try:
                self.on_ack(consumer_id, result)
            except Exception:
                pass
        return result

    def resync_watermark(self, consumer_id: str) -> dict[str, Any]:
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return {
                    "resync_generation": 0,
                    "resync_head_seq": 0,
                    "resync_head_event_time": "",
                    "last_ack_sequence": 0,
                    "fanout_last_ack": 0,
                    "fanout_last_market": 0,
                    "fanout_last_tick": 0,
                    "fanout_source": "",
                    "physical_reader_invalidated": False,
                    "physical_reader_generation": 0,
                    "physical_reader_sequence": 0,
                    "first_post_resync_seq": 0,
                    "ring_cursor": 0,
                    "ring_handoff_reason": "",
                }
            return {
                "resync_generation": int(st.resync_generation),
                "resync_head_seq": int(st.resync_head_seq),
                "resync_head_event_time": str(st.resync_head_event_time or ""),
                "last_ack_sequence": int(st.last_ack_sequence),
                "fanout_last_ack": int(st.fanout_last_ack),
                "fanout_last_market": int(st.fanout_last_market),
                "fanout_last_tick": int(st.fanout_last_tick),
                "fanout_source": str(st.fanout_source or ""),
                "physical_reader_invalidated": bool(st.physical_reader_invalidated),
                "physical_reader_generation": int(st.physical_reader_generation),
                "physical_reader_sequence": int(st.physical_reader_sequence),
                "first_post_resync_seq": int(st.first_post_resync_seq),
                "ring_cursor": int(st.fanout_last_tick),
                "ring_handoff_reason": str(st.ring_handoff_reason or ""),
                "resync_to_first_fanout_ms": (
                    round((float(st.first_post_resync_mono) - float(st.resync_commit_mono)) * 1000.0, 3)
                    if float(st.first_post_resync_mono) > 0.0 and float(st.resync_commit_mono) > 0.0
                    else None
                ),
            }

    def _ring_handoff_locked(self, head_seq: int) -> tuple[int, str, str]:
        """Map REALTIME head onto a live-ring tick. Caller holds _lock.

        Never returns last_tick=0 when the publisher has produced ticks: that
        would skip the ring and start a forward-only Capture scan.
        Head-not-in-ring is explicit (successor tick, or wait at current _tick).
        Historical JSONL seek from file 0 is not a fallback.
        """
        head = int(head_seq)
        head_tick = 0
        first_after_tick = 0
        for tick, env in self._ring:
            seq = 0
            if env.kind == KIND_MARKET_PUSH:
                try:
                    seq = int(env.sequence or 0)
                except Exception:
                    seq = 0
            if seq == head:
                head_tick = int(tick)
            elif seq > head and first_after_tick == 0:
                first_after_tick = int(tick)
        if head_tick > 0:
            return head_tick, FANOUT_SOURCE_RING, "head_in_ring"
        if first_after_tick > 0:
            return max(0, first_after_tick - 1), FANOUT_SOURCE_RING, "successor_in_ring"
        wait_tick = int(self._tick)
        if wait_tick <= 0:
            wait_tick = max(0, int(self._ring_start_tick) - 1)
        return wait_tick, FANOUT_SOURCE_RING, "wait_next_publish"

    def _apply_tcp_fanout_resync(
        self,
        consumer_id: str,
        *,
        last_ack: int,
        last_market: int,
        last_tick: int,
        applied_gen: int,
    ) -> tuple[int, int, int, int]:
        """Jump local fanout cursors to the committed resync head.

        last_tick is the ring tick of that head (or a wait/successor tick).
        It is never forced to 0: that starved the live ring on 20260825.
        """
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return last_ack, last_market, last_tick, applied_gen
            gen = int(st.resync_generation or 0)
            head = int(st.resync_head_seq or 0)
            if gen > int(applied_gen) and head > 0:
                st.fanout_last_ack = head
                st.fanout_last_market = head
                tick = int(st.fanout_last_tick or 0)
                if tick <= 0:
                    tick, source, reason = self._ring_handoff_locked(head)
                    st.fanout_last_tick = tick
                    st.fanout_source = source
                    st.ring_handoff_reason = reason
                st.physical_reader_invalidated = True
                st.physical_reader_generation = gen
                st.physical_reader_sequence = 0
                return head, head, int(st.fanout_last_tick), gen
        return last_ack, last_market, last_tick, applied_gen

    def _apply_inproc_fanout_resync(
        self,
        *,
        last_ack: int,
        last_market: int,
        last_tick: int,
        applied_gen: int,
    ) -> tuple[int, int, int, int]:
        with self._lock:
            jumped = False
            head = int(last_market)
            max_gen = int(applied_gen)
            tick = int(last_tick)
            for st in self._consumers.values():
                gen = int(st.resync_generation or 0)
                seq = int(st.resync_head_seq or 0)
                if gen > int(applied_gen) and seq > 0:
                    jumped = True
                    max_gen = max(max_gen, gen)
                    head = max(head, seq)
                    st.fanout_last_ack = seq
                    st.fanout_last_market = seq
                    ht, source, reason = self._ring_handoff_locked(seq)
                    st.fanout_last_tick = ht
                    st.fanout_source = source
                    st.ring_handoff_reason = reason
                    st.physical_reader_invalidated = True
                    st.physical_reader_generation = gen
                    st.physical_reader_sequence = 0
                    tick = max(tick, ht)
            if jumped:
                return head, head, int(tick), max_gen
        return last_ack, last_market, last_tick, applied_gen

    def _resync_abort_check(self, *, applied_gen: int, consumer_id: str = "") -> AbortCheck:
        def _check() -> bool:
            with self._lock:
                if consumer_id:
                    st = self._consumers.get(consumer_id)
                    if st is not None and int(st.resync_generation or 0) > int(applied_gen):
                        return True
                    return False
                for st in self._consumers.values():
                    if int(st.resync_generation or 0) > int(applied_gen):
                        return True
            return False

        return _check

    def _realtime_floor_for(self, consumer_id: str, applied_gen: int) -> int:
        if int(applied_gen) <= 0:
            return 0
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return 0
            if int(st.resync_generation or 0) <= 0:
                return 0
            return int(st.resync_head_seq or 0)

    def _inproc_realtime_floor(self, applied_gen: int) -> int:
        if int(applied_gen) <= 0:
            return 0
        with self._lock:
            floor = 0
            for st in self._consumers.values():
                if int(st.resync_generation or 0) > 0:
                    floor = max(floor, int(st.resync_head_seq or 0))
            return floor


    def note_delivered(self, consumer_id: str, sequence: int) -> None:
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return
            seq = int(sequence)
            st.last_delivered_sequence = max(st.last_delivered_sequence, seq)
            st.lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))
            head = int(st.resync_head_seq or 0)
            if head > 0 and seq > head:
                if int(st.first_post_resync_seq or 0) == 0:
                    st.first_post_resync_seq = seq
                    st.first_post_resync_mono = time.monotonic()
                st.fanout_source = FANOUT_SOURCE_RING
                st.physical_reader_sequence = seq

    def last_delivered_sequence(self, consumer_id: str) -> int:
        with self._lock:
            st = self._consumers.get(consumer_id)
            return int(st.last_delivered_sequence if st else 0)

    def last_ack_sequence(self, consumer_id: str) -> int:
        with self._lock:
            st = self._consumers.get(consumer_id)
            return int(st.last_ack_sequence if st else 0)

    def consumer_lag(self, consumer_id: str) -> int:
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return int(self._seq_published)
            return max(0, int(self._seq_published) - int(st.last_ack_sequence))

    def consumer_health(self) -> dict[str, Any]:
        with self._lock:
            out = {}
            for cid, st in self._consumers.items():
                lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))
                st.lag = lag
                out[cid] = {
                    "last_ack": st.last_ack_sequence,
                    "last_ack_sequence": st.last_ack_sequence,
                    "last_delivered": st.last_delivered_sequence,
                    "last_delivered_sequence": st.last_delivered_sequence,
                    "last_ack_at": st.last_ack_at,
                    "lag": lag,
                    "connected": st.connected,
                    "ready": st.ready,
                    "transport": st.transport,
                    "errors": st.errors,
                    "last_error": st.last_error,
                    "resync_generation": int(st.resync_generation),
                    "resync_head_seq": int(st.resync_head_seq),
                    "resync_head_event_time": str(st.resync_head_event_time or ""),
                    "fanout_last_ack": int(st.fanout_last_ack),
                    "fanout_last_market": int(st.fanout_last_market),
                    "fanout_last_tick": int(st.fanout_last_tick),
                    "fanout_source": str(st.fanout_source or ""),
                    "physical_reader_invalidated": bool(st.physical_reader_invalidated),
                    "physical_reader_generation": int(st.physical_reader_generation),
                    "physical_reader_sequence": int(st.physical_reader_sequence),
                    "first_post_resync_seq": int(st.first_post_resync_seq),
                    "ring_cursor": int(st.fanout_last_tick),
                    "ring_handoff_reason": str(st.ring_handoff_reason or ""),
                }
            return out

    def publisher_health(self) -> dict[str, Any]:
        with self._lock:
            tcp_n = len(self._tcp_clients)
            return {
                "publish_ok": self.publish_ok,
                "publish_fail": self.publish_fail,
                "gap_count": self.gap_count,
                "last_published_sequence": self._seq_published,
                "tcp_clients": tcp_n,
                "inproc_consumers": len(self._handlers),
                "listening": self.listening,
                "ack_reject_count": self.ack_reject_count,
                "persist_publish_decoupled": True,
                "slow_client_isolated": True,
                "disk_backed_catchup": self._capture_dir is not None,
                "ring_size": int(self.ring_size),
                "ring_len": len(self._ring),
                "ring_evict_count": int(self.ring_evict_count),
                "disk_catchup_reads": int(self.disk_catchup_reads),
                "disk_stale_resync_aborts": int(self.disk_stale_resync_aborts),
                "stale_disk_reads_after_resync": int(self.stale_disk_reads_after_resync),
                "capture_dir": str(self._capture_dir or ""),
            }

    def max_lag(self) -> int:
        with self._lock:
            if not self._consumers:
                return int(self._seq_published)
            return max(
                max(0, int(self._seq_published) - int(st.last_ack_sequence))
                for st in self._consumers.values()
            )

    def should_block_entry_for_lag(self) -> bool:
        return self.max_lag() >= int(self.lag_entry_block)

    def paper_tcp_ready(self, consumer_id: str = "paper_runtime") -> bool:
        with self._lock:
            st = self._consumers.get(consumer_id)
            return bool(st and st.connected and st.ready and st.transport == "TCP")

    def publish(self, event: MarketEnvelope) -> bool:
        """Offer event to the ring and wake fanout threads. Never blocks on clients."""
        event.published_at = event.published_at or now_iso()
        handlers: list[tuple[str, Callable[[MarketEnvelope], None]]] = []
        inline = False
        with self._lock:
            self._tick += 1
            tick = int(self._tick)
            self._ring.append((tick, event))
            while len(self._ring) > self.ring_size:
                old_tick, old_env = self._ring.popleft()
                self.ring_evict_count += 1
                self._ring_start_tick = old_tick + 1
                if old_env.kind == KIND_MARKET_PUSH:
                    seq = int(old_env.sequence or 0)
                    cached = self._market_by_seq.get(seq)
                    if cached is old_env:
                        self._market_by_seq.pop(seq, None)
            if not self._ring:
                self._ring_start_tick = tick + 1
            else:
                self._ring_start_tick = int(self._ring[0][0])
            if event.kind == KIND_MARKET_PUSH and int(event.sequence) > 0:
                self._market_by_seq[int(event.sequence)] = event
                self._seq_published = max(self._seq_published, int(event.sequence))
            elif event.kind not in (KIND_GAP,) and int(event.sequence) > 0:
                self._seq_published = max(self._seq_published, int(event.sequence))
            self.publish_ok += 1
            if self._cond is not None:
                self._cond.notify_all()
            worker_alive = self._inproc_thread is not None and self._inproc_thread.is_alive()
            if not worker_alive:
                handlers = list(self._handlers.items())
                inline = True
        if inline:
            for cid, handler in handlers:
                self._call_inproc_handler(cid, handler, event)
        return True

    def lookup_market_envelope(
        self,
        sequence: int,
        reader: Optional[CaptureSequenceReader] = None,
        *,
        abort_check: Optional[AbortCheck] = None,
        consumer_id: str = "",
        realtime_floor_seq: int = 0,
        max_scan_records: Optional[int] = None,
    ) -> Optional[MarketEnvelope]:
        seq = int(sequence)
        floor = int(realtime_floor_seq or 0)
        if floor > 0:
            # REALTIME: never forward-scan Capture JSONL at or before the head.
            return None
        with self._lock:
            env = self._market_by_seq.get(seq)
            if env is not None:
                return env
            st = self._consumers.get(consumer_id) if consumer_id else None
            head = int(st.resync_head_seq or 0) if st is not None else 0
            gen = int(st.resync_generation or 0) if st is not None else 0
        if reader is None or reader.invalidated:
            return None
        chunk = DISK_LOOKUP_CHUNK if max_scan_records is None else int(max_scan_records)
        got = reader.get(seq, abort_check=abort_check, max_scan_records=chunk)
        if reader.aborted:
            with self._lock:
                self.disk_stale_resync_aborts += 1
                if st is None and consumer_id:
                    st = self._consumers.get(consumer_id)
                if st is not None:
                    st.reader_abort_count = int(st.reader_abort_count) + 1
                    st.physical_reader_sequence = int(reader.last_seq)
            return None
        if got is not None:
            with self._lock:
                self.disk_catchup_reads += 1
                if gen > 0 and seq <= head:
                    self.stale_disk_reads_after_resync += 1
                if consumer_id:
                    cst = self._consumers.get(consumer_id)
                    if cst is not None:
                        cst.physical_reader_sequence = int(got.sequence)
                        cst.fanout_source = FANOUT_SOURCE_DISK
            return got
        if reader.last_lookup_status == "chunk_limit" and consumer_id:
            with self._lock:
                cst = self._consumers.get(consumer_id)
                if cst is not None:
                    cst.physical_reader_sequence = int(reader.last_seq)
        return None

    def _call_inproc_handler(
        self,
        cid: str,
        handler: Callable[[MarketEnvelope], None],
        event: MarketEnvelope,
    ) -> None:
        try:
            handler(event)
            if int(event.sequence) > 0 and event.kind == KIND_MARKET_PUSH:
                self.note_delivered(cid, int(event.sequence))
        except Exception as exc:
            with self._lock:
                st = self._consumers.get(cid)
                if st:
                    st.errors += 1
                    st.last_error = type(exc).__name__
                self.publish_fail += 1

    def _inproc_loop(self) -> None:
        last_tick = 0
        last_market = 0
        last_ack = 0
        applied_gen = 0
        reader: Optional[CaptureSequenceReader] = None
        if self._capture_dir is not None:
            reader = CaptureSequenceReader(self._capture_dir)
        try:
            while not self._stop.is_set():
                with self._lock:
                    if self._cond is not None:
                        self._cond.wait(timeout=0.2)
                    handlers = list(self._handlers.items())
                    head_tick = int(self._tick)
                if not handlers:
                    last_tick = head_tick
                    continue
                prev_gen = applied_gen
                last_ack, last_market, last_tick, applied_gen = self._apply_inproc_fanout_resync(
                    last_ack=last_ack,
                    last_market=last_market,
                    last_tick=last_tick,
                    applied_gen=applied_gen,
                )
                if applied_gen > prev_gen and reader is not None:
                    reader.invalidate()
                while not self._stop.is_set():
                    prev_gen = applied_gen
                    last_ack, last_market, last_tick, applied_gen = self._apply_inproc_fanout_resync(
                        last_ack=last_ack,
                        last_market=last_market,
                        last_tick=last_tick,
                        applied_gen=applied_gen,
                    )
                    if applied_gen > prev_gen and reader is not None:
                        reader.invalidate()
                    floor = self._inproc_realtime_floor(applied_gen)
                    nxt = self._next_fanout_event(
                        last_tick=last_tick,
                        last_market=last_market,
                        reader=reader,
                        last_ack=last_ack,
                        realtime_floor_seq=floor,
                        abort_check=self._resync_abort_check(applied_gen=applied_gen),
                    )
                    if nxt is None:
                        break
                    env, last_tick, last_market = nxt
                    for cid, handler in handlers:
                        self._call_inproc_handler(cid, handler, env)
        finally:
            if reader is not None:
                reader.close()

    def _next_fanout_event(
        self,
        *,
        last_tick: int,
        last_market: int,
        reader: Optional[CaptureSequenceReader],
        last_ack: int = 0,
        realtime_floor_seq: int = 0,
        abort_check: Optional[AbortCheck] = None,
        consumer_id: str = "",
    ) -> Optional[tuple[MarketEnvelope, int, int]]:
        """Return (env, new_tick, new_last_market) or None if nothing is ready.

        REALTIME (floor>0): live ring only. Stale Capture catch-up cannot starve
        the publisher. CONTINUE (floor=0): disk catch-up then live, unchanged.
        """
        floor = int(realtime_floor_seq or 0)
        with self._lock:
            ring_start = int(self._ring_start_tick)
            if floor > 0:
                start_tick = int(last_tick)
                if start_tick < ring_start:
                    start_tick = ring_start - 1
                for tick, env in self._ring:
                    if tick <= start_tick:
                        continue
                    if env.kind == KIND_MARKET_PUSH:
                        try:
                            seq = int(env.sequence or 0)
                        except Exception:
                            seq = 0
                        if seq <= floor:
                            continue
                        return env, int(tick), seq
                    return env, int(tick), last_market
                return None
            if last_tick > 0 and last_tick >= ring_start:
                for tick, env in self._ring:
                    if tick > last_tick:
                        new_market = last_market
                        if env.kind == KIND_MARKET_PUSH and int(env.sequence) > 0:
                            new_market = int(env.sequence)
                        return env, int(tick), new_market
                return None
        want = max(int(last_ack), int(last_market)) + 1
        env = self.lookup_market_envelope(
            want,
            reader,
            abort_check=abort_check,
            consumer_id=consumer_id,
            realtime_floor_seq=0,
        )
        if env is None:
            return None
        joined_tick = last_tick
        with self._lock:
            for tick, ring_env in self._ring:
                if ring_env.kind == KIND_MARKET_PUSH and int(ring_env.sequence) == int(env.sequence):
                    joined_tick = int(tick)
                    break
        return env, joined_tick, int(env.sequence)

    def publish_gap(self, *, from_seq: int, to_seq: int, reason: str, session_id: str) -> None:
        self.gap_count += 1
        gap = MarketEnvelope(
            kind=KIND_GAP,
            ingress_session_id=session_id,
            sequence=int(to_seq),
            event_time=now_iso(),
            received_at=now_iso(),
            persisted_at="",
            published_at=now_iso(),
            symbol="",
            payload={},
            connection_generation=0,
            registration_generation=0,
            meta={"from_seq": int(from_seq), "to_seq": int(to_seq), "reason": reason},
        )
        self.publish(gap)

    def _drop_tcp_sock_locked(self, sock: socket.socket) -> None:
        try:
            sock.close()
        except Exception:
            pass
        if sock in self._tcp_clients:
            self._tcp_clients.remove(sock)
        for cid, s in list(self._tcp_by_consumer.items()):
            if s is sock:
                self._tcp_by_consumer.pop(cid, None)
                st = self._consumers.get(cid)
                if st:
                    st.connected = False
                    st.ready = False

    def _serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.host, int(self.port)))
            srv.listen(8)
            srv.settimeout(0.5)
            self._server = srv
            self.listening = True
        except Exception:
            try:
                srv.close()
            except Exception:
                pass
            self.listening = False
            return
        while not self._stop.is_set():
            try:
                conn, _addr = srv.accept()
                conn.settimeout(2.0)
                t = threading.Thread(
                    target=self._tcp_client_loop,
                    args=(conn,),
                    name="market-bus-tcp-client",
                    daemon=True,
                )
                self._readers.append(t)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                if self._stop.is_set():
                    break
                continue

    def _encode_envelope(self, event: MarketEnvelope) -> bytes:
        return (json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    def _tcp_client_loop(self, conn: socket.socket) -> None:
        """Handshake subscribe, then pull-fanout + ACK read on this client thread only."""
        buf = b""
        consumer_id = ""
        registered = False
        resume_mode = RESUME_MODE_CONTINUE
        reader: Optional[CaptureSequenceReader] = None
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not registered and not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    if str(obj.get("msg_type") or "") == MSG_SUBSCRIBE:
                        consumer_id = str(obj.get("consumer_id") or "paper_runtime")
                        resume_mode = str(obj.get("resume_mode") or RESUME_MODE_CONTINUE).lower()
                        req_ack = 0
                        try:
                            req_ack = int(obj.get("resume_from_ack") or obj.get("last_ack_sequence") or 0)
                        except Exception:
                            req_ack = 0
                        with self._lock:
                            prev = self._tcp_by_consumer.get(consumer_id)
                            if prev is not None and prev is not conn:
                                self._drop_tcp_sock_locked(prev)
                            self._tcp_clients.append(conn)
                            self._tcp_by_consumer[consumer_id] = conn
                            st = self._consumers.get(consumer_id) or ConsumerState(consumer_id=consumer_id)
                            st.connected = True
                            st.ready = True
                            st.transport = "TCP"
                            st.ingress_session_id = self.ingress_session_id
                            if resume_mode == RESUME_MODE_REALTIME:
                                head = int(self._seq_published)
                                st.last_ack_sequence = head
                                st.last_delivered_sequence = max(st.last_delivered_sequence, head)
                                st.lag = 0
                            else:
                                if req_ack > int(st.last_ack_sequence):
                                    st.last_ack_sequence = min(int(req_ack), int(self._seq_published))
                            last_ack = int(st.last_ack_sequence)
                            pub_head = int(self._seq_published)
                            self._consumers[consumer_id] = st
                        ready = {
                            "msg_type": MSG_READY,
                            "consumer_id": consumer_id,
                            "ingress_session_id": self.ingress_session_id,
                            "last_ack_sequence": last_ack,
                            "publisher_last_sequence": pub_head,
                            "resume_mode": resume_mode,
                            "disk_backed_catchup": self._capture_dir is not None,
                            "at": now_iso(),
                        }
                        conn.sendall(
                            (json.dumps(ready, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                        )
                        registered = True
                        break
            if not registered:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            last_ack = int(self.last_ack_sequence(consumer_id))
            last_tick = 0
            last_market = last_ack
            applied_gen = 0
            if resume_mode == RESUME_MODE_REALTIME:
                last_tick = int(self._tick)
                last_market = int(self._seq_published)
            if self._capture_dir is not None:
                reader = CaptureSequenceReader(self._capture_dir)
            conn.settimeout(0.05)
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line.decode("utf-8", errors="replace"))
                        except Exception:
                            continue
                        if str(obj.get("msg_type") or "") != MSG_ACK:
                            continue
                        self.ack(
                            str(obj.get("consumer_id") or consumer_id),
                            int(obj.get("sequence") or 0),
                            ingress_session_id=str(obj.get("ingress_session_id") or ""),
                            processed_at=str(obj.get("processed_at") or ""),
                            resync_commit=bool(obj.get("resync_commit")),
                            resync_head_event_time=str(obj.get("resync_head_event_time") or ""),
                            resync_generation=int(obj.get("resync_generation") or 0),
                        )
                except socket.timeout:
                    pass
                except OSError:
                    break
                prev_gen = applied_gen
                last_ack, last_market, last_tick, applied_gen = self._apply_tcp_fanout_resync(
                    consumer_id,
                    last_ack=last_ack,
                    last_market=last_market,
                    last_tick=last_tick,
                    applied_gen=applied_gen,
                )
                if applied_gen > prev_gen and reader is not None:
                    reader.invalidate()
                sent = 0
                while not self._stop.is_set() and sent < 64:
                    prev_gen = applied_gen
                    last_ack, last_market, last_tick, applied_gen = self._apply_tcp_fanout_resync(
                        consumer_id,
                        last_ack=last_ack,
                        last_market=last_market,
                        last_tick=last_tick,
                        applied_gen=applied_gen,
                    )
                    if applied_gen > prev_gen and reader is not None:
                        reader.invalidate()
                    floor = self._realtime_floor_for(consumer_id, applied_gen)
                    nxt = self._next_fanout_event(
                        last_tick=last_tick,
                        last_market=last_market,
                        reader=reader,
                        last_ack=last_ack,
                        realtime_floor_seq=floor,
                        abort_check=self._resync_abort_check(
                            applied_gen=applied_gen, consumer_id=consumer_id
                        ),
                        consumer_id=consumer_id,
                    )
                    if nxt is None:
                        break
                    env, last_tick, last_market = nxt
                    try:
                        conn.settimeout(1.0)
                        conn.sendall(self._encode_envelope(env))
                        conn.settimeout(0.05)
                    except Exception:
                        return
                    if env.kind == KIND_MARKET_PUSH and int(env.sequence) > 0:
                        self.note_delivered(consumer_id, int(env.sequence))
                    sent += 1
        finally:
            if reader is not None:
                reader.close()
            with self._lock:
                self._drop_tcp_sock_locked(conn)


@dataclass
class LocalMarketBusConsumer:
    """TCP consumer used by Paper Runtime under MARKET_INGRESS_V2."""

    consumer_id: str = "paper_runtime"
    host: str = DEFAULT_BUS_HOST
    port: int = DEFAULT_BUS_PORT
    connect_timeout_sec: float = 5.0
    on_envelope: Optional[Callable[[MarketEnvelope], None]] = None
    ingress_session_id: str = ""
    resume_mode: str = RESUME_MODE_CONTINUE
    resume_from_ack: int = 0

    _sock: Optional[socket.socket] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    last_sequence: int = 0
    last_event_at: str = ""
    last_ack_sequence: int = 0
    publisher_last_sequence_hint: int = 0
    connected: bool = False
    ready: bool = False
    transport: str = "TCP"
    messages: int = 0
    parse_errors: int = 0
    ack_ok: int = 0
    ack_fail: int = 0
    last_error: str = ""
    entry_blocked: bool = False
    entry_block_reason: str = ""
    last_resume_mode: str = ""

    def connect(self) -> bool:
        try:
            s = socket.create_connection((self.host, int(self.port)), timeout=self.connect_timeout_sec)
            s.settimeout(2.0)
            sub = {
                "msg_type": MSG_SUBSCRIBE,
                "consumer_id": self.consumer_id,
                "ingress_session_id": self.ingress_session_id,
                "resume_mode": str(self.resume_mode or RESUME_MODE_CONTINUE),
                "resume_from_ack": int(self.resume_from_ack or 0),
                "at": now_iso(),
            }
            s.sendall((json.dumps(sub, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            # Wait READY
            buf = b""
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    obj = json.loads(line.decode("utf-8", errors="replace"))
                    if str(obj.get("msg_type") or "") == MSG_READY:
                        new_sid = str(obj.get("ingress_session_id") or "")
                        prev_sid = str(self.ingress_session_id or "")
                        hint = 0
                        try:
                            hint = int(obj.get("last_ack_sequence") or 0)
                        except Exception:
                            hint = 0
                        try:
                            self.publisher_last_sequence_hint = int(
                                obj.get("publisher_last_sequence") or 0
                            )
                        except Exception:
                            self.publisher_last_sequence_hint = 0
                        self.last_resume_mode = str(obj.get("resume_mode") or "")
                        # New Ingress session ⇒ reset ACK cursor (do not keep prior-session high watermark)
                        if new_sid and new_sid != prev_sid:
                            self.last_ack_sequence = hint
                        else:
                            self.last_ack_sequence = max(int(self.last_ack_sequence or 0), hint)
                        self.ingress_session_id = new_sid or prev_sid
                        self._sock = s
                        self.connected = True
                        self.ready = True
                        self.transport = "TCP"
                        # stash leftover for reader
                        self._pending_buf = buf
                        return True
                    # unexpected early envelope — keep buffered
                    self._pending_buf = line + b"\n" + buf
                    self._sock = s
                    self.connected = True
                    self.ready = True
                    return True
            self.last_error = "subscribe_ready_timeout"
            try:
                s.close()
            except Exception:
                pass
            return False
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}:{exc}"
            self.connected = False
            self.ready = False
            return False

    def start(self) -> None:
        self._stop.clear()
        if not self.connected:
            self.connect()
        self._thread = threading.Thread(target=self._run, name=f"bus-consumer-{self.consumer_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self.connected = False
        self.ready = False

    def send_ack(
        self,
        sequence: int,
        *,
        ingress_session_id: str = "",
        processed_at: str = "",
        resync_commit: bool = False,
        resync_head_event_time: str = "",
        resync_generation: int = 0,
    ) -> bool:
        """Send ACK only after successful processing."""
        with self._lock:
            sock = self._sock
            if sock is None or not self.connected:
                self.ack_fail += 1
                self.last_error = "ack_not_connected"
                return False
            msg = {
                "msg_type": MSG_ACK,
                "consumer_id": self.consumer_id,
                "ingress_session_id": ingress_session_id or self.ingress_session_id,
                "sequence": int(sequence),
                "processed_at": processed_at or now_iso(),
            }
            if resync_commit:
                msg["resync_commit"] = True
                msg["resync_head_event_time"] = str(resync_head_event_time or "")
                msg["resync_generation"] = int(resync_generation or 0)
            try:
                sock.sendall(
                    (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                )
                self.last_ack_sequence = max(self.last_ack_sequence, int(sequence))
                self.ack_ok += 1
                return True
            except Exception as exc:
                self.ack_fail += 1
                self.last_error = type(exc).__name__
                self.connected = False
                self.ready = False
                return False

    def health(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "connected": self.connected,
            "ready": self.ready,
            "transport": self.transport,
            "last_sequence": self.last_sequence,
            "last_ack_sequence": self.last_ack_sequence,
            "last_event_at": self.last_event_at,
            "messages": self.messages,
            "parse_errors": self.parse_errors,
            "ack_ok": self.ack_ok,
            "ack_fail": self.ack_fail,
            "entry_blocked": self.entry_blocked,
            "entry_block_reason": self.entry_block_reason,
            "last_error": self.last_error,
            "market_source": "LOCAL_MARKET_BUS",
            "ingress_session_id": self.ingress_session_id,
        }

    def _run(self) -> None:
        buf = getattr(self, "_pending_buf", b"")
        while not self._stop.is_set():
            if self._sock is None:
                if not self.connect():
                    time.sleep(1.0)
                    continue
                buf = getattr(self, "_pending_buf", b"")
            assert self._sock is not None
            try:
                chunk = self._sock.recv(65536)
                if not chunk:
                    self.connected = False
                    self.ready = False
                    try:
                        self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
                    time.sleep(0.5)
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception:
                        self.parse_errors += 1
                        continue
                    # Ignore control ready echoes
                    if str(obj.get("msg_type") or "") in (MSG_READY, MSG_SUBSCRIBE, MSG_ACK):
                        continue
                    try:
                        env = MarketEnvelope.from_dict(obj)
                    except Exception:
                        self.parse_errors += 1
                        continue
                    self.messages += 1
                    self.last_sequence = int(env.sequence)
                    self.last_event_at = env.event_time or env.received_at
                    if env.kind == "entry_block":
                        self.entry_blocked = True
                        self.entry_block_reason = env.entry_block_reason or str(
                            (env.meta or {}).get("reason") or "ingress_entry_block"
                        )
                    elif env.kind == "entry_unblock":
                        self.entry_blocked = False
                        self.entry_block_reason = ""
                    if self.on_envelope is not None:
                        try:
                            self.on_envelope(env)
                        except Exception as exc:
                            self.last_error = type(exc).__name__
            except socket.timeout:
                continue
            except Exception as exc:
                self.last_error = type(exc).__name__
                self.connected = False
                self.ready = False
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                self._sock = None
                time.sleep(0.5)

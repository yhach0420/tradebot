"""Local Market Bus — Ingress publisher → Paper/Observer consumers + ACK.

Backpressure:
- Raw Writer is NOT a consumer (upstream of publish).
- Slow consumers never block WS receive / Raw write.
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
from typing import Any, Callable, Deque, Optional

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


@dataclass
class AckResult:
    ok: bool
    reason: str = ""
    last_ack_sequence: int = 0
    lag: int = 0


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
    _seq_published: int = field(default=0, init=False)
    _ring: Deque[MarketEnvelope] = field(default_factory=deque, init=False)
    _consumers: dict[str, ConsumerState] = field(default_factory=dict, init=False)
    _handlers: dict[str, Callable[[MarketEnvelope], None]] = field(default_factory=dict, init=False)
    _tcp_clients: list[socket.socket] = field(default_factory=list, init=False)
    _tcp_by_consumer: dict[str, socket.socket] = field(default_factory=dict, init=False)
    _server: Optional[socket.socket] = field(default=None, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _readers: list[threading.Thread] = field(default_factory=list, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    publish_ok: int = 0
    publish_fail: int = 0
    gap_count: int = 0
    ack_reject_count: int = 0
    listening: bool = False
    on_ack: Optional[Callable[[str, AckResult], None]] = None

    def start(self) -> None:
        self._stop.clear()
        if self.enable_tcp:
            self._thread = threading.Thread(target=self._serve, name="market-bus-pub", daemon=True)
            self._thread.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not self.listening:
                time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()
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
    ) -> AckResult:
        """Single ACK path for inproc + TCP. Rejects unknown/stale/future/reverse."""
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
            result = AckResult(
                ok=True,
                reason="ok",
                last_ack_sequence=st.last_ack_sequence,
                lag=st.lag,
            )
        if self.on_ack is not None and result.ok:
            try:
                self.on_ack(consumer_id, result)
            except Exception:
                pass
        return result

    def note_delivered(self, consumer_id: str, sequence: int) -> None:
        with self._lock:
            st = self._consumers.get(consumer_id)
            if st is None:
                return
            st.last_delivered_sequence = max(st.last_delivered_sequence, int(sequence))
            st.lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))

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
        """Deliver to in-proc handlers + TCP clients. Never raises to caller."""
        event.published_at = event.published_at or now_iso()
        line = (json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with self._lock:
            self._ring.append(event)
            while len(self._ring) > self.ring_size:
                self._ring.popleft()
            if event.kind not in (KIND_GAP,):
                # control events may reuse sequence; only advance on positive seq
                if int(event.sequence) > 0:
                    self._seq_published = max(self._seq_published, int(event.sequence))
            handlers = list(self._handlers.items())
            clients = list(self._tcp_clients)
            tcp_map = dict(self._tcp_by_consumer)
            for cid, handler in handlers:
                st = self._consumers.get(cid)
                try:
                    handler(event)
                    if st and int(event.sequence) > 0:
                        st.last_delivered_sequence = int(event.sequence)
                        st.lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))
                except Exception as exc:
                    if st:
                        st.errors += 1
                        st.last_error = type(exc).__name__
                    self.publish_fail += 1
            # Mark delivered for TCP consumers before send
            for cid, sock in tcp_map.items():
                st = self._consumers.get(cid)
                if st and int(event.sequence) > 0:
                    st.last_delivered_sequence = max(st.last_delivered_sequence, int(event.sequence))
                    st.lag = max(0, int(self._seq_published) - int(st.last_ack_sequence))
            dead: list[socket.socket] = []
            for sock in clients:
                try:
                    sock.sendall(line)
                except Exception:
                    dead.append(sock)
            for sock in dead:
                self._drop_tcp_sock_locked(sock)
            self.publish_ok += 1
        return True

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

    def _tcp_client_loop(self, conn: socket.socket) -> None:
        """Handshake subscribe, then read ACKs while connection lives."""
        buf = b""
        consumer_id = ""
        registered = False
        try:
            # Wait for subscribe
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
                        catchup: list[MarketEnvelope] = []
                        with self._lock:
                            # Duplicate consumer: replace prior TCP socket for same consumer_id.
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
                            # REALTIME_RESYNC: jump ACK to publisher head (OPEN=0 path; Paper asserts).
                            if resume_mode == RESUME_MODE_REALTIME:
                                head = int(self._seq_published)
                                st.last_ack_sequence = head
                                st.last_delivered_sequence = max(st.last_delivered_sequence, head)
                                st.lag = 0
                                catchup = []
                            else:
                                # Optional client resume watermark (never regress; never beyond head).
                                if req_ack > int(st.last_ack_sequence):
                                    st.last_ack_sequence = min(int(req_ack), int(self._seq_published))
                                last_ack = int(st.last_ack_sequence)
                                catchup = [
                                    e
                                    for e in list(self._ring)
                                    if int(e.sequence) > last_ack and e.kind == KIND_MARKET_PUSH
                                ]
                            self._consumers[consumer_id] = st
                        with self._lock:
                            st_ack = self._consumers.get(consumer_id)
                            last_ack_hint = int(st_ack.last_ack_sequence if st_ack else 0)
                            pub_head = int(self._seq_published)
                        ready = {
                            "msg_type": MSG_READY,
                            "consumer_id": consumer_id,
                            "ingress_session_id": self.ingress_session_id,
                            "last_ack_sequence": last_ack_hint,
                            "publisher_last_sequence": pub_head,
                            "resume_mode": resume_mode,
                            "at": now_iso(),
                        }
                        conn.sendall(
                            (json.dumps(ready, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                        )
                        for e in catchup:
                            line_out = (
                                json.dumps(e.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
                            ).encode("utf-8")
                            conn.sendall(line_out)
                            with self._lock:
                                st2 = self._consumers.get(consumer_id)
                                if st2:
                                    st2.last_delivered_sequence = max(
                                        st2.last_delivered_sequence, int(e.sequence)
                                    )
                                    st2.lag = max(
                                        0, int(self._seq_published) - int(st2.last_ack_sequence)
                                    )
                        registered = True
                        break
            if not registered:
                try:
                    conn.close()
                except Exception:
                    pass
                return
            conn.settimeout(1.0)
            # Read ACKs
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
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
                    )
        finally:
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

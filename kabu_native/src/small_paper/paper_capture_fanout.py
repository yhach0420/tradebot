"""Phase687W24 — Paper → Capture localhost fan-out (single PUSH source).

Paper remains the sole Kabu WebSocket consumer. Capture Sidecar ingests
JSONL payloads on 127.0.0.1 and writes push_part_*.jsonl.

Fail-open on Paper: fan-out errors never stop ENTRY/EXIT evaluation.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

DEFAULT_FANOUT_HOST = "127.0.0.1"
DEFAULT_FANOUT_PORT = 18724
ENV_FANOUT_PORT = "TRADEBOT_CAPTURE_FANOUT_PORT"
ENV_FANOUT_DISABLE = "TRADEBOT_CAPTURE_FANOUT_DISABLE"


def fanout_enabled(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_FANOUT_DISABLE, "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return False
    return True


def fanout_port(*, environ: Optional[Mapping[str, str]] = None) -> int:
    env = environ if environ is not None else os.environ
    try:
        return int(str(env.get(ENV_FANOUT_PORT, "") or DEFAULT_FANOUT_PORT))
    except Exception:
        return DEFAULT_FANOUT_PORT


@dataclass
class PaperCaptureFanoutStats:
    sent: int = 0
    send_errors: int = 0
    connect_errors: int = 0
    last_error: str = ""


@dataclass
class PaperCaptureFanoutClient:
    """Best-effort localhost JSONL sender used by Paper live PUSH loop."""

    host: str = DEFAULT_FANOUT_HOST
    port: int = DEFAULT_FANOUT_PORT
    connect_timeout_sec: float = 0.2
    send_timeout_sec: float = 0.5
    stats: PaperCaptureFanoutStats = field(default_factory=PaperCaptureFanoutStats)
    _sock: Optional[socket.socket] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _disabled: bool = False

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def _ensure_sock(self) -> Optional[socket.socket]:
        if self._disabled:
            return None
        if self._sock is not None:
            return self._sock
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.connect_timeout_sec)
            s.settimeout(self.send_timeout_sec)
            self._sock = s
            return s
        except Exception as exc:
            self.stats.connect_errors += 1
            self.stats.last_error = f"{type(exc).__name__}:{exc}"
            return None

    def send_payload(self, payload: Mapping[str, Any]) -> bool:
        if not fanout_enabled() or self._disabled:
            return False
        line = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            sock = self._ensure_sock()
            if sock is None:
                return False
            try:
                sock.sendall(data)
                self.stats.sent += 1
                return True
            except Exception as exc:
                self.stats.send_errors += 1
                self.stats.last_error = f"{type(exc).__name__}:{exc}"
                try:
                    sock.close()
                except Exception:
                    pass
                self._sock = None
                return False


_GLOBAL_CLIENT: Optional[PaperCaptureFanoutClient] = None
_GLOBAL_LOCK = threading.Lock()


def get_paper_capture_fanout() -> PaperCaptureFanoutClient:
    global _GLOBAL_CLIENT
    with _GLOBAL_LOCK:
        if _GLOBAL_CLIENT is None:
            _GLOBAL_CLIENT = PaperCaptureFanoutClient(port=fanout_port())
        return _GLOBAL_CLIENT


def fanout_push_payload(payload: Mapping[str, Any]) -> bool:
    """Paper hot path helper — never raises."""
    try:
        if not fanout_enabled():
            return False
        return get_paper_capture_fanout().send_payload(payload)
    except Exception:
        return False


@dataclass
class CaptureFanoutServerStats:
    accepted_connections: int = 0
    messages: int = 0
    enqueue_ok: int = 0
    enqueue_fail: int = 0
    parse_errors: int = 0
    last_error: str = ""


class CaptureFanoutIngestServer:
    """Localhost JSONL ingest server owned by Capture Sidecar (writer process)."""

    def __init__(
        self,
        *,
        on_payload: Callable[[dict[str, Any]], None],
        host: str = DEFAULT_FANOUT_HOST,
        port: int = DEFAULT_FANOUT_PORT,
    ) -> None:
        self.host = host
        self.port = int(port)
        self._on_payload = on_payload
        self.stats = CaptureFanoutServerStats()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[socket.socket] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="capture-fanout-ingest", daemon=True)
        self._thread.start()
        # brief readiness wait
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self._server is None:
            time.sleep(0.01)

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        srv.settimeout(0.5)
        self._server = srv
        try:
            while not self._stop.is_set():
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.stats.accepted_connections += 1
                threading.Thread(
                    target=self._handle_conn,
                    args=(conn,),
                    name="capture-fanout-conn",
                    daemon=True,
                ).start()
        finally:
            try:
                srv.close()
            except Exception:
                pass

    def _handle_conn(self, conn: socket.socket) -> None:
        buf = b""
        conn.settimeout(1.0)
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    self.stats.messages += 1
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception as exc:
                        self.stats.parse_errors += 1
                        self.stats.last_error = f"{type(exc).__name__}:{exc}"
                        continue
                    if not isinstance(obj, dict):
                        self.stats.parse_errors += 1
                        continue
                    try:
                        self._on_payload(obj)
                        self.stats.enqueue_ok += 1
                    except Exception as exc:
                        self.stats.enqueue_fail += 1
                        self.stats.last_error = f"{type(exc).__name__}:{exc}"
        finally:
            try:
                conn.close()
            except Exception:
                pass

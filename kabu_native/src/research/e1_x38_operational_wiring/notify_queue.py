"""Non-blocking notification queue (Discord critical path isolation)."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NotifyItem:
    kind: str  # ENTRY / FILL / EXIT / EXPIRED / LATENCY_WARNING / PBV2_SHADOW / V1R_1M_SHADOW
    payload: dict[str, Any]
    enqueue_time: float
    sent_time: Optional[float] = None
    prefix: str = ""


class NonBlockingNotifyQueue:
    """
    Enqueue never waits on Discord HTTP.
    Background thread drains queue (mock send = sleep(0) + stamp).
    Trading logic must only call enqueue().
    """

    def __init__(self, *, maxsize: int = 10_000):
        self._q: deque[NotifyItem] = deque()
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._dropped = 0
        self._sent = 0
        self._enqueued = 0
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="x38-notify", daemon=True)
        self._worker.start()
        self._max_backlog = 0

    def enqueue(self, kind: str, payload: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
        t0 = time.perf_counter()
        item = NotifyItem(kind=kind, payload=dict(payload), enqueue_time=time.time(), prefix=prefix)
        with self._lock:
            if len(self._q) >= self._maxsize:
                self._dropped += 1
                return {
                    "queued": False,
                    "status": "DROPPED_OVERFLOW",
                    "enqueue_latency_ms": (time.perf_counter() - t0) * 1000.0,
                    "blocking": False,
                }
            self._q.append(item)
            self._enqueued += 1
            self._max_backlog = max(self._max_backlog, len(self._q))
        # critical path ends here — no HTTP
        return {
            "queued": True,
            "status": "QUEUED",
            "enqueue_latency_ms": (time.perf_counter() - t0) * 1000.0,
            "blocking": False,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            item = None
            with self._lock:
                if self._q:
                    item = self._q.popleft()
            if item is None:
                time.sleep(0.001)
                continue
            # mock Discord send (non-trading thread)
            time.sleep(0.0005)
            item.sent_time = time.time()
            with self._lock:
                self._sent += 1

    def flush(self, timeout_sec: float = 2.0) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            with self._lock:
                if not self._q:
                    return
            time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()
        self._worker.join(timeout=2.0)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enqueued": self._enqueued,
                "sent": self._sent,
                "dropped": self._dropped,
                "backlog": len(self._q),
                "max_backlog": self._max_backlog,
                "notification_blocking_on_critical_path": False,
            }


def format_entry_prefix() -> str:
    return "[V1R PROSPECTIVE ENTRY]"


def format_fill_prefix() -> str:
    return "[V1R PROSPECTIVE FILL]"


def format_exit_prefix() -> str:
    return "[V1R PROSPECTIVE EXIT]"


def format_expired_prefix() -> str:
    return "[V1R ENTRY EXPIRED]"


def format_pbv2_shadow_prefix() -> str:
    return "[PBV2 SHADOW]"


def format_1m_shadow_prefix() -> str:
    return "[V1R 1M SHADOW]"

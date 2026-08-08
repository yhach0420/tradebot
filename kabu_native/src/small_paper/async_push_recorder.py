"""Async PushRecorder wrapper — keep ACK path free of sync disk I/O."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from storage.push_recorder import PushRecorder


@dataclass
class AsyncPushRecorder:
    """Background append; drop-oldest on overflow (never blocks ACK path)."""

    inner: PushRecorder
    maxsize: int = 50000
    _q: queue.Queue = field(init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    dropped: int = 0
    written: int = 0
    errors: int = 0

    def __post_init__(self) -> None:
        self._q = queue.Queue(maxsize=int(self.maxsize))

    @property
    def day_dir(self) -> Path:
        return self.inner.day_dir

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="async-push-recorder", daemon=True)
        self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 2.0) -> None:
        if drain:
            # best-effort drain
            deadline = threading.Event()
            _ = deadline
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def append(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        recorded_at: datetime | None = None,
        source: str = "push",
    ) -> Path:
        self.start()
        item = (str(symbol), dict(payload), recorded_at, str(source))
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except Exception:
                pass
            self.dropped += 1
            try:
                self._q.put_nowait(item)
            except Exception:
                self.dropped += 1
        return self.inner.path_for_symbol(symbol)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._q.empty():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    break
                continue
            sym, payload, recorded_at, source = item
            try:
                self.inner.append(sym, payload, recorded_at=recorded_at, source=source)
                self.written += 1
            except Exception:
                self.errors += 1

    def summarize(self, symbols: list[str]) -> dict[str, Any]:
        s = self.inner.summarize(symbols)
        s["async_dropped"] = self.dropped
        s["async_written"] = self.written
        s["async_errors"] = self.errors
        s["async_queue_depth"] = self._q.qsize()
        return s

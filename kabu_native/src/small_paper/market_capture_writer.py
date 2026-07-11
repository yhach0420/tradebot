"""Phase687W9 — Append-only market capture writer (no trading imports).

Buffered JSONL writer with rotation, flush/fsync policy, and gap accounting.
Does not import entry/exit/SafetySM/canonical/Discord trade paths.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SCHEMA_VERSION = "687W9.1"
WRITER_VERSION = "687W9.1"

ROTATE_BYTES = 256 * 1024 * 1024
ROTATE_SEC = 30 * 60
FLUSH_MS = 250
FLUSH_RECORDS = 100
FSYNC_SEC = 5.0
DEFAULT_QUEUE_MAX = 50_000

SECRET_KEYS = frozenset(
    {
        "password",
        "api_password",
        "apipassword",
        "token",
        "authorization",
        "account",
        "accountnumber",
        "holdid",
        "orderid",
        "clienttoken",
    }
)

MAX_EXCLUSIVE_PART_ATTEMPTS = 32


def list_push_part_indexes(output_dir: Path) -> list[int]:
    """Parse push_part_NNNN.jsonl indexes (ignore malformed names)."""
    indexes: list[int] = []
    root = Path(output_dir)
    if not root.is_dir():
        return indexes
    for p in root.glob("push_part_*.jsonl"):
        name = p.name
        if not name.startswith("push_part_") or not name.endswith(".jsonl"):
            continue
        mid = name[len("push_part_") : -len(".jsonl")]
        if not mid.isdigit():
            continue
        indexes.append(int(mid))
    return sorted(indexes)


def next_exclusive_part_index(output_dir: Path, *, start_from: Optional[int] = None) -> int:
    existing = list_push_part_indexes(output_dir)
    if existing:
        nxt = max(existing) + 1
        if start_from is not None:
            return max(start_from, nxt)
        return nxt
    return int(start_from or 1)


def _now_jst() -> datetime:
    return datetime.now(JST)


def _iso_jst(dt: Optional[datetime] = None) -> str:
    return (dt or _now_jst()).isoformat(timespec="milliseconds")


def _iso_utc(dt: Optional[datetime] = None) -> str:
    d = dt or _now_jst()
    return d.astimezone(tz=None).astimezone().isoformat(timespec="milliseconds")


def mask_secrets(obj: Any) -> Any:
    """Recursively redact secret-like keys; never store tokens/passwords."""
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower().replace("-", "").replace("_", "")
            if lk in SECRET_KEYS or "password" in lk or lk.endswith("token"):
                out[k] = "[REDACTED]"
            else:
                out[k] = mask_secrets(v)
        return out
    if isinstance(obj, list):
        return [mask_secrets(x) for x in obj]
    return obj


def extract_board_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Search helpers only — original_payload remains source of truth."""
    return {
        "symbol": payload.get("Symbol") or payload.get("symbol"),
        "exchange": payload.get("Exchange") or payload.get("exchange"),
        "current_price": payload.get("CurrentPrice") or payload.get("current_price"),
        "current_price_time": payload.get("CurrentPriceTime") or payload.get("current_price_time"),
        "trading_volume": payload.get("TradingVolume") or payload.get("trading_volume"),
        "trading_value": payload.get("TradingValue") or payload.get("trading_value"),
        "bid": payload.get("BidPrice") or payload.get("bid"),
        "ask": payload.get("AskPrice") or payload.get("ask"),
    }


@dataclass
class WriterStats:
    enqueued: int = 0
    written: int = 0
    dropped: int = 0
    emergency_appends: int = 0
    queue_overflows: int = 0
    queue_high_water: int = 0
    rotate_count: int = 0
    flush_count: int = 0
    fsync_count: int = 0
    bytes_written: int = 0
    malformed: int = 0
    status: str = "ONLINE"
    last_error: str = ""


@dataclass
class MarketCaptureWriter:
    """Dedicated-thread buffered append-only JSONL writer."""

    output_dir: Path
    capture_session_id: str
    queue_max: int = DEFAULT_QUEUE_MAX
    rotate_bytes: int = ROTATE_BYTES
    rotate_sec: float = float(ROTATE_SEC)
    flush_ms: int = FLUSH_MS
    flush_records: int = FLUSH_RECORDS
    fsync_sec: float = FSYNC_SEC

    _queue: Deque[dict[str, Any]] = field(default_factory=deque, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _cv: threading.Condition = field(init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _part_idx: int = field(default=1, init=False)
    _part_path: Optional[Path] = field(default=None, init=False)
    _part_fh: Any = field(default=None, init=False)
    _part_bytes: int = field(default=0, init=False)
    _part_opened_at: float = field(default=0.0, init=False)
    _since_flush: int = field(default=0, init=False)
    _last_flush: float = field(default=0.0, init=False)
    _last_fsync: float = field(default=0.0, init=False)
    _seq: int = field(default=0, init=False)
    stats: WriterStats = field(default_factory=WriterStats)

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cv = threading.Condition(self._lock)
        self._open_part()

    def _part_name(self) -> str:
        return f"push_part_{self._part_idx:04d}.jsonl"

    def _open_part(self, *, exclusive: bool = False) -> None:
        if self._part_fh is not None:
            try:
                self._part_fh.flush()
                os.fsync(self._part_fh.fileno())
                self._part_fh.close()
            except Exception:
                pass
            self._part_fh = None

        attempts = 0
        last_err = ""
        while attempts < MAX_EXCLUSIVE_PART_ATTEMPTS:
            attempts += 1
            self._part_path = self.output_dir / self._part_name()
            try:
                if exclusive:
                    fd = os.open(
                        str(self._part_path),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o644,
                    )
                    self._part_fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
                    self._part_bytes = 0
                else:
                    self._part_fh = self._part_path.open("a", encoding="utf-8", newline="\n")
                    self._part_bytes = self._part_path.stat().st_size if self._part_path.is_file() else 0
                self._part_opened_at = time.monotonic()
                self._since_flush = 0
                now = time.monotonic()
                self._last_flush = now
                self._last_fsync = now
                return
            except FileExistsError:
                last_err = "FileExistsError"
                self._part_idx = next_exclusive_part_index(
                    self.output_dir, start_from=self._part_idx + 1
                )
                continue
            except Exception as exc:
                last_err = type(exc).__name__
                self.stats.status = "CAPTURE_WRITE_FAILED"
                self.stats.last_error = last_err
                raise
        self.stats.status = "CAPTURE_WRITE_FAILED"
        self.stats.last_error = last_err or "exclusive_part_exhausted"
        raise OSError(
            f"CAPTURE_WRITE_FAILED: could not open exclusive part after {attempts} attempts"
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="market-capture-writer", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._drain_locked_write()
        if self._part_fh is not None:
            try:
                self._part_fh.flush()
                os.fsync(self._part_fh.fileno())
                self._part_fh.close()
            except Exception:
                pass
            self._part_fh = None

    def enqueue(self, original_payload: Mapping[str, Any], *, mono_ns: Optional[int] = None) -> bool:
        """Non-blocking enqueue. On overflow: emergency append or gap+drop (never raise to Paper)."""
        try:
            record = self._build_record(original_payload, mono_ns=mono_ns)
        except Exception:
            self.stats.malformed += 1
            return False

        with self._cv:
            self.stats.enqueued += 1
            if len(self._queue) >= self.queue_max:
                self.stats.queue_overflows += 1
                self.stats.status = "DEGRADED"
                # try emergency direct append outside queue
                ok = self._emergency_append(record)
                if ok:
                    self.stats.emergency_appends += 1
                    self._cv.notify()
                    return True
                self.stats.dropped += 1
                self._append_gap(
                    {
                        "reason": "queue_overflow",
                        "dropped": 1,
                        "queue_size": len(self._queue),
                        "at": _iso_jst(),
                    }
                )
                self._cv.notify()
                return False
            self._queue.append(record)
            hw = len(self._queue)
            if hw > self.stats.queue_high_water:
                self.stats.queue_high_water = hw
            self._cv.notify()
            return True

    def _build_record(self, payload: Mapping[str, Any], *, mono_ns: Optional[int]) -> dict[str, Any]:
        now = _now_jst()
        with self._lock:
            self._seq += 1
            seq = self._seq
        clean = mask_secrets(dict(payload))
        fields = extract_board_fields(clean if isinstance(clean, dict) else {})
        return {
            "schema_version": SCHEMA_VERSION,
            "capture_session_id": self.capture_session_id,
            "sequence": seq,
            "received_at_jst": _iso_jst(now),
            "received_at_utc": now.astimezone().isoformat(timespec="milliseconds"),
            "received_monotonic_ns": int(mono_ns if mono_ns is not None else time.monotonic_ns()),
            **fields,
            "original_payload": clean,
        }

    def _emergency_append(self, record: dict[str, Any]) -> bool:
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            path = self._part_path or (self.output_dir / self._part_name())
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self.stats.written += 1
            self.stats.bytes_written += len(line.encode("utf-8"))
            return True
        except Exception as exc:
            self.stats.last_error = type(exc).__name__
            return False

    def _append_gap(self, gap: Mapping[str, Any]) -> None:
        path = self.output_dir / "capture_gaps.jsonl"
        try:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(dict(gap), ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def append_disconnect(self, event: Mapping[str, Any]) -> None:
        path = self.output_dir / "disconnect_events.jsonl"
        try:
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                if not self._queue:
                    self._cv.wait(timeout=self.flush_ms / 1000.0)
                batch: list[dict[str, Any]] = []
                while self._queue and len(batch) < self.flush_records:
                    batch.append(self._queue.popleft())
            for rec in batch:
                self._write_one(rec)
            self._maybe_flush_fsync_rotate()

    def _drain_locked_write(self) -> None:
        with self._lock:
            while self._queue:
                rec = self._queue.popleft()
                self._write_one(rec)
            self._maybe_flush_fsync_rotate(force=True)

    def _write_one(self, record: dict[str, Any]) -> None:
        if self._part_fh is None:
            self._open_part()
        try:
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            assert self._part_fh is not None
            self._part_fh.write(line)
            n = len(line.encode("utf-8"))
            self._part_bytes += n
            self.stats.bytes_written += n
            self.stats.written += 1
            self._since_flush += 1
        except Exception as exc:
            self.stats.status = "DEGRADED"
            self.stats.last_error = type(exc).__name__
            self.stats.dropped += 1
            self._append_gap({"reason": "write_failed", "error": type(exc).__name__, "at": _iso_jst()})

    def _maybe_flush_fsync_rotate(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if self._part_fh is None:
            return
        need_flush = force or self._since_flush >= self.flush_records or (now - self._last_flush) * 1000 >= self.flush_ms
        if need_flush:
            try:
                self._part_fh.flush()
                self.stats.flush_count += 1
                self._since_flush = 0
                self._last_flush = now
            except Exception as exc:
                self.stats.last_error = type(exc).__name__
        if force or (now - self._last_fsync) >= self.fsync_sec:
            try:
                os.fsync(self._part_fh.fileno())
                self.stats.fsync_count += 1
                self._last_fsync = now
            except Exception:
                pass
        age = now - self._part_opened_at
        if self._part_bytes >= self.rotate_bytes or age >= self.rotate_sec:
            self._part_idx += 1
            self.stats.rotate_count += 1
            self._open_part()

    def new_part_after_restart(self) -> dict[str, Any]:
        """Restart policy: never append to existing parts — exclusive max(index)+1."""
        import hashlib

        previous_last = self._part_idx
        existing = list_push_part_indexes(self.output_dir)
        prev_hashes: dict[str, str] = {}
        for idx in existing:
            p = self.output_dir / f"push_part_{idx:04d}.jsonl"
            try:
                h = hashlib.sha256()
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                prev_hashes[p.name] = h.hexdigest()
            except Exception:
                prev_hashes[p.name] = ""
        self._part_idx = next_exclusive_part_index(self.output_dir)
        self.stats.rotate_count += 1
        self._open_part(exclusive=True)
        meta = {
            "previous_last_part": previous_last,
            "previous_existing_parts": existing,
            "new_part": self._part_idx,
            "new_part_path": str(self._part_path) if self._part_path else "",
            "previous_part_hashes": prev_hashes,
            "started_at": _iso_jst(),
            "policy": "exclusive_max_plus_one",
        }
        try:
            (self.output_dir / "restart_part_manifest.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:
            pass
        return meta

    def snapshot_stats(self) -> dict[str, Any]:
        return {
            "enqueued": self.stats.enqueued,
            "written": self.stats.written,
            "dropped": self.stats.dropped,
            "emergency_appends": self.stats.emergency_appends,
            "queue_overflows": self.stats.queue_overflows,
            "queue_high_water": self.stats.queue_high_water,
            "rotate_count": self.stats.rotate_count,
            "flush_count": self.stats.flush_count,
            "fsync_count": self.stats.fsync_count,
            "bytes_written": self.stats.bytes_written,
            "malformed": self.stats.malformed,
            "status": self.stats.status,
            "last_error": self.stats.last_error,
            "part_index": self._part_idx,
            "sequence": self._seq,
            "writer_version": WRITER_VERSION,
            "schema_version": SCHEMA_VERSION,
        }

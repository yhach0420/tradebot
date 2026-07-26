"""Ingress Raw Writer — session-isolated JSONL, never appends prior sessions."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.market_ingress_protocol import now_iso

JST = ZoneInfo("Asia/Tokyo")
ROTATE_BYTES = 256 * 1024 * 1024
ROTATE_SEC = 30 * 60
WRITER_VERSION = "ingress_raw_v2.1"


def trading_date_jst(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(JST)
    return d.strftime("%Y%m%d")


def session_dir(native_root: Path, trading_date: str, ingress_session_id: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / trading_date / f"session_{ingress_session_id}"


@dataclass
class RawWriteResult:
    ok: bool
    sequence: int = 0
    part_name: str = ""
    raw_record_id: str = ""
    persisted_at: str = ""
    error: str = ""
    bytes_written: int = 0


@dataclass
class MarketRawWriter:
    """Append-only within a *new* session directory; never opens existing non-empty foreign parts."""

    output_dir: Path
    ingress_session_id: str
    rotate_bytes: int = ROTATE_BYTES
    rotate_sec: float = float(ROTATE_SEC)

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _part_idx: int = field(default=1, init=False)
    _part_fh: Any = field(default=None, init=False)
    _part_path: Optional[Path] = field(default=None, init=False)
    _part_bytes: int = field(default=0, init=False)
    _part_opened_at: float = field(default=0.0, init=False)
    _seq: int = field(default=0, init=False)
    written: int = 0
    dropped: int = 0
    storage_errors: int = 0
    last_error: str = ""
    last_write_at: str = ""
    status: str = "ONLINE"

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Hard rule: refuse if directory already has non-empty push parts (wrong reuse).
        for p in sorted(self.output_dir.glob("push_part_*.jsonl")):
            if p.stat().st_size > 0:
                raise RuntimeError(
                    f"INGRESS_RAW_SESSION_COLLISION: non-empty part exists in {self.output_dir}: {p.name}"
                )
        self._open_part_exclusive()

    def _part_name(self) -> str:
        return f"push_part_{self._part_idx:04d}.jsonl"

    def _open_part_exclusive(self) -> None:
        if self._part_fh is not None:
            try:
                self._part_fh.flush()
                os.fsync(self._part_fh.fileno())
                self._part_fh.close()
            except Exception:
                pass
            self._part_fh = None
        # Find free index
        while True:
            path = self.output_dir / self._part_name()
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                self._part_fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
                self._part_path = path
                self._part_bytes = 0
                self._part_opened_at = time.monotonic()
                return
            except FileExistsError:
                self._part_idx += 1
                if self._part_idx > 10_000:
                    raise RuntimeError("INGRESS_RAW_PART_EXHAUSTED")

    def write_envelope_record(self, record: dict[str, Any]) -> RawWriteResult:
        with self._lock:
            try:
                if self._part_fh is None:
                    self._open_part_exclusive()
                self._seq += 1
                seq = self._seq
                persisted = now_iso()
                record = dict(record)
                record["sequence"] = seq
                record["persisted_at"] = persisted
                record["ingress_session_id"] = self.ingress_session_id
                record["raw_record_id"] = f"{self.ingress_session_id}:{seq}"
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                assert self._part_fh is not None
                self._part_fh.write(line)
                self._part_fh.flush()
                n = len(line.encode("utf-8"))
                self._part_bytes += n
                self.written += 1
                self.last_write_at = persisted
                age = time.monotonic() - self._part_opened_at
                if self._part_bytes >= self.rotate_bytes or age >= self.rotate_sec:
                    self._part_idx += 1
                    self._open_part_exclusive()
                return RawWriteResult(
                    ok=True,
                    sequence=seq,
                    part_name=self._part_name() if self._part_path is None else self._part_path.name,
                    raw_record_id=record["raw_record_id"],
                    persisted_at=persisted,
                    bytes_written=n,
                )
            except Exception as exc:
                self.storage_errors += 1
                self.dropped += 1
                self.status = "STORAGE_BLOCKED"
                self.last_error = type(exc).__name__
                return RawWriteResult(ok=False, error=type(exc).__name__)

    def close(self) -> None:
        with self._lock:
            if self._part_fh is not None:
                try:
                    self._part_fh.flush()
                    os.fsync(self._part_fh.fileno())
                    self._part_fh.close()
                except Exception:
                    pass
                self._part_fh = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "ingress_session_id": self.ingress_session_id,
            "written": self.written,
            "dropped": self.dropped,
            "storage_errors": self.storage_errors,
            "last_error": self.last_error,
            "last_write_at": self.last_write_at,
            "last_sequence": self._seq,
            "part_index": self._part_idx,
            "status": self.status,
            "writer_version": WRITER_VERSION,
            "output_dir": str(self.output_dir),
        }

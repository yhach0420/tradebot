"""Incremental output writer for full-session live dry-run.

V12: high-frequency audit is enqueued to a writer thread (no per-PUSH
open/append/close). Critical events keep durability (flush) without
blocking ACK for a full fsync-on-every-candidate storm.
"""

from __future__ import annotations

import csv
import json
import queue
import threading
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CRITICAL_EVENT_TYPES = frozenset(
    {
        "accepted",
        "entry",
        "exit",
        "fill",
        "expired",
        "pending",
        "session_close",
        "error",
        "safety_fail",
        "safety_halt",
        "official_entry",
        "position_close",
        "session_force_close",
    }
)
CRITICAL_KINDS = frozenset(
    {
        "V1R_FILL",
        "V1R_EXPIRED",
        "V1R_ENTRY",
        "V1R_EXIT",
        "PENDING",
        "FILL",
        "EXPIRED",
        "ENTRY",
        "EXIT",
        "SESSION_CLOSE",
        "ERROR",
        "SAFETY_FAIL",
        "RECOVERY_ENTER",
        "RECOVERY_EVAL",
        "RECOVERY_EXIT",
    }
)

_STOP = object()


def _is_critical_event(event: Mapping[str, Any]) -> bool:
    et = str(event.get("event_type") or "").strip().lower()
    if et in CRITICAL_EVENT_TYPES:
        return True
    kind = str(event.get("kind") or "").strip()
    if kind in CRITICAL_KINDS:
        return True
    status = str(event.get("status") or "").strip().upper()
    if status in CRITICAL_KINDS:
        return True
    return False


class LiveSessionWriter:
    """Append JSONL incrementally; flush summary on heartbeat and exit."""

    def __init__(
        self,
        output_dir: Path,
        *,
        incremental: bool,
        event_fields: Sequence[str],
        async_io: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.incremental = incremental
        self.event_fields = list(event_fields)
        self.async_io = bool(async_io and incremental)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = output_dir / "small_paper_events.jsonl"
        self._errors_path = output_dir / "errors.jsonl"
        self._heartbeat_path = output_dir / "heartbeat.jsonl"
        self._events_csv_initialized = False
        self._reject_csv_initialized = False
        self._handles: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._q: queue.Queue[Any] = queue.Queue(maxsize=200_000)
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._dropped = 0
        if self.async_io:
            self._thread = threading.Thread(
                target=self._writer_loop,
                name="live-session-audit-writer",
                daemon=True,
            )
            self._thread.start()

    def append_event(self, event: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        item = ("event", dict(event), _is_critical_event(event))
        self._enqueue(item, critical=item[2])

    def append_position_row(self, row: Mapping[str, Any], *, fields: Sequence[str]) -> None:
        if not self.incremental:
            return
        self._enqueue(("position", dict(row), list(fields)), critical=False)

    def append_error(self, record: Mapping[str, Any]) -> None:
        self._enqueue(("error", dict(record)), critical=True)

    def append_volume_shadow_eval(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "volume_gate_shadow_eval.jsonl", dict(record), False), critical=False)

    def append_np_pre_entry_features(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "np_pre_entry_features.jsonl", dict(record), False), critical=False)

    def append_np_pre_entry_outcomes(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "np_pre_entry_outcomes.jsonl", dict(record), False), critical=False)

    def append_live_order_intent(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_order_intent.jsonl", dict(record), False), critical=False)

    def append_live_order_state(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_order_state.jsonl", dict(record), False), critical=False)

    def append_live_position_reconcile(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_position_reconcile.jsonl", dict(record), False), critical=False)

    def append_live_order_latency(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_order_latency.jsonl", dict(record), False), critical=False)

    def append_live_order_would_send(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_order_would_send.jsonl", dict(record), False), critical=False)

    def append_live_capital_check(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_capital_check.jsonl", dict(record), False), critical=False)

    def append_live_order_event(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        self._enqueue(("jsonl", "live_order_event.jsonl", dict(record), False), critical=False)

    def append_live_order_error(self, record: Mapping[str, Any]) -> None:
        self._enqueue(("jsonl", "live_order_error.jsonl", dict(record), True), critical=True)

    def append_entry_scan_audit(self, record: Mapping[str, Any]) -> None:
        self._enqueue(("jsonl", "entry_scan_audit.jsonl", dict(record), False), critical=False)

    def append_discord_entry_delivery(self, record: Mapping[str, Any]) -> None:
        self._enqueue(("jsonl", "discord_entry_delivery.jsonl", dict(record), False), critical=False)

    def append_heartbeat(self, record: Mapping[str, Any]) -> None:
        row = dict(record)
        try:
            from small_paper.session_runtime_identity import stamp_session_identity

            stamp_session_identity(row, session_id=self.output_dir.name)
        except Exception:
            pass
        self._enqueue(("heartbeat", row), critical=True)

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        self.flush(timeout=2.0)
        body = dict(summary)
        try:
            from small_paper.session_runtime_identity import stamp_session_identity

            stamp_session_identity(body, session_id=self.output_dir.name)
        except Exception:
            pass
        (self.output_dir / "small_paper_summary.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def finalize_batch(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        positions: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
        pos_fields: Sequence[str],
    ) -> None:
        """Rewrite CSV/JSONL when not incremental; always flush summary."""
        self.flush(timeout=10.0)
        self.write_summary(summary)
        if self.incremental:
            self._write_csv(self.output_dir / "small_paper_positions.csv", pos_fields, positions)
            self.close()
            return
        with (self.output_dir / "small_paper_events.jsonl").open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(dict(e), ensure_ascii=False) + "\n")
        self._write_csv(self.output_dir / "small_paper_events.csv", self.event_fields, events)
        rejects = [e for e in events if e.get("event_type") == "rejected"]
        self._write_csv(self.output_dir / "small_paper_rejects.csv", self.event_fields, rejects)
        self._write_csv(self.output_dir / "small_paper_positions.csv", pos_fields, positions)
        self.close()

    def flush(self, timeout: float = 5.0) -> None:
        if not self.async_io:
            with self._lock:
                for fh in self._handles.values():
                    try:
                        fh.flush()
                    except Exception:
                        pass
            return
        done = threading.Event()
        try:
            self._q.put(("flush", done), timeout=1.0)
        except queue.Full:
            self._dropped += 1
            return
        done.wait(timeout=max(0.05, float(timeout)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.async_io and self._thread is not None:
            try:
                self._q.put(_STOP, timeout=1.0)
            except queue.Full:
                pass
            self._thread.join(timeout=8.0)
        with self._lock:
            for fh in self._handles.values():
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass
            self._handles.clear()

    def dropped_count(self) -> int:
        return int(self._dropped)

    def _enqueue(self, item: Any, *, critical: bool) -> None:
        if self._closed:
            self._apply(item)
            return
        if not self.async_io:
            self._apply(item)
            return
        try:
            if critical:
                self._q.put(item, timeout=0.25)
            else:
                self._q.put_nowait(item)
        except queue.Full:
            if critical:
                # Last resort: write on caller thread so PENDING/FILL/ERROR are not lost.
                self._apply(item)
            else:
                self._dropped += 1

    def _writer_loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is _STOP:
                while True:
                    try:
                        extra = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if extra is _STOP:
                        continue
                    if isinstance(extra, tuple) and extra and extra[0] == "flush":
                        extra[1].set()
                        continue
                    self._apply(extra)
                break
            if isinstance(item, tuple) and item and item[0] == "flush":
                with self._lock:
                    for fh in self._handles.values():
                        try:
                            fh.flush()
                        except Exception:
                            pass
                item[1].set()
                continue
            self._apply(item)

    def _fh(self, path: Path, *, newline: Optional[str] = None) -> Any:
        key = str(path)
        fh = self._handles.get(key)
        if fh is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = path.open("a", encoding="utf-8", newline=newline)
            self._handles[key] = fh
        return fh

    def _write_jsonl_line(self, path: Path, obj: Mapping[str, Any], *, flush: bool) -> None:
        line = json.dumps(dict(obj), ensure_ascii=False) + "\n"
        if self.async_io:
            fh = self._fh(path)
            fh.write(line)
            if flush:
                fh.flush()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            if flush:
                fh.flush()

    def _apply(self, item: Any) -> None:
        if not item:
            return
        kind = item[0]
        with self._lock:
            if kind == "event":
                event = item[1]
                critical = bool(item[2]) if len(item) > 2 else False
                self._write_jsonl_line(self._events_path, event, flush=critical)
                self._append_csv_row_locked(
                    self.output_dir / "small_paper_events.csv",
                    self.event_fields,
                    event,
                    init_flag="_events_csv_initialized",
                )
                if event.get("event_type") == "rejected":
                    self._append_csv_row_locked(
                        self.output_dir / "small_paper_rejects.csv",
                        self.event_fields,
                        event,
                        init_flag="_reject_csv_initialized",
                    )
            elif kind == "position":
                self._append_csv_row_locked(
                    self.output_dir / "small_paper_positions.csv",
                    item[2],
                    item[1],
                    init_flag="_positions_csv_initialized",
                )
            elif kind == "error":
                self._write_jsonl_line(self._errors_path, item[1], flush=True)
            elif kind == "heartbeat":
                self._write_jsonl_line(self._heartbeat_path, item[1], flush=True)
            elif kind == "jsonl":
                _, filename, record, critical = item
                self._write_jsonl_line(
                    self.output_dir / str(filename), record, flush=bool(critical)
                )

    def _append_csv_row_locked(
        self,
        path: Path,
        fields: Sequence[str],
        row: Mapping[str, Any],
        *,
        init_flag: str,
    ) -> None:
        write_header = not getattr(self, init_flag, False) and not path.is_file()
        if self.async_io:
            fh = self._fh(path, newline="")
            w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
            if write_header:
                w.writeheader()
                setattr(self, init_flag, True)
            w.writerow({k: row.get(k, "") for k in fields})
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
            if write_header:
                w.writeheader()
                setattr(self, init_flag, True)
            w.writerow({k: row.get(k, "") for k in fields})

    def _append_csv_row(
        self,
        path: Path,
        fields: Sequence[str],
        row: Mapping[str, Any],
        *,
        init_flag: str,
    ) -> None:
        with self._lock:
            self._append_csv_row_locked(path, fields, row, init_flag=init_flag)

    @staticmethod
    def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

"""Incremental output writer for full-session live dry-run."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


class LiveSessionWriter:
    """Append JSONL incrementally; flush summary on heartbeat and exit."""

    def __init__(self, output_dir: Path, *, incremental: bool, event_fields: Sequence[str]) -> None:
        self.output_dir = output_dir
        self.incremental = incremental
        self.event_fields = list(event_fields)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = output_dir / "small_paper_events.jsonl"
        self._errors_path = output_dir / "errors.jsonl"
        self._heartbeat_path = output_dir / "heartbeat.jsonl"
        self._events_csv_initialized = False
        self._reject_csv_initialized = False

    def append_event(self, event: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        with self._events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
        self._append_csv_row(
            self.output_dir / "small_paper_events.csv",
            self.event_fields,
            event,
            init_flag="_events_csv_initialized",
        )
        if event.get("event_type") == "rejected":
            self._append_csv_row(
                self.output_dir / "small_paper_rejects.csv",
                self.event_fields,
                event,
                init_flag="_reject_csv_initialized",
            )

    def append_position_row(self, row: Mapping[str, Any], *, fields: Sequence[str]) -> None:
        if not self.incremental:
            return
        self._append_csv_row(
            self.output_dir / "small_paper_positions.csv",
            fields,
            row,
            init_flag="_positions_csv_initialized",
        )

    def append_error(self, record: Mapping[str, Any]) -> None:
        with self._errors_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_volume_shadow_eval(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "volume_gate_shadow_eval.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_order_intent(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_order_intent.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_order_state(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_order_state.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_position_reconcile(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_position_reconcile.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_order_latency(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_order_latency.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_order_would_send(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_order_would_send.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_live_capital_check(self, record: Mapping[str, Any]) -> None:
        if not self.incremental:
            return
        path = self.output_dir / "live_capital_check.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_entry_scan_audit(self, record: Mapping[str, Any]) -> None:
        path = self.output_dir / "entry_scan_audit.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def append_heartbeat(self, record: Mapping[str, Any]) -> None:
        with self._heartbeat_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(record), ensure_ascii=False) + "\n")

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        (self.output_dir / "small_paper_summary.json").write_text(
            json.dumps(dict(summary), ensure_ascii=False, indent=2),
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
        self.write_summary(summary)
        if self.incremental:
            self._write_csv(self.output_dir / "small_paper_positions.csv", pos_fields, positions)
            return
        with (self.output_dir / "small_paper_events.jsonl").open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(dict(e), ensure_ascii=False) + "\n")
        self._write_csv(self.output_dir / "small_paper_events.csv", self.event_fields, events)
        rejects = [e for e in events if e.get("event_type") == "rejected"]
        self._write_csv(self.output_dir / "small_paper_rejects.csv", self.event_fields, rejects)
        self._write_csv(self.output_dir / "small_paper_positions.csv", pos_fields, positions)

    def _append_csv_row(
        self,
        path: Path,
        fields: Sequence[str],
        row: Mapping[str, Any],
        *,
        init_flag: str,
    ) -> None:
        write_header = not getattr(self, init_flag, False) and not path.is_file()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
            if write_header:
                w.writeheader()
                setattr(self, init_flag, True)
            w.writerow({k: row.get(k, "") for k in fields})

    @staticmethod
    def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

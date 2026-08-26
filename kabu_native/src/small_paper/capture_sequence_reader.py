"""Read persisted Ingress JSONL by sequence (live catch-up SoT).

Capture files are the overflow backlog. This reader does not own lifecycle,
does not write, and does not create a second Ingress. Concurrent read while
MarketRawWriter appends is required so publish/sendall never blocks persist.

REALTIME resync must invalidate this reader. Forward-only scan from file 0
to find a later head is forbidden in REALTIME; CONTINUE sequential replay
uses bounded chunks so a resync ACK can be observed between chunks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope

AbortCheck = Callable[[], bool]


def envelope_from_raw_record(
    rec: dict[str, Any],
    *,
    part_name: str = "",
) -> Optional[MarketEnvelope]:
    if not isinstance(rec, dict):
        return None
    try:
        seq = int(rec.get("sequence") or 0)
    except Exception:
        return None
    if seq <= 0:
        return None
    payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
    if payload is None:
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    kind = str(rec.get("kind") or KIND_MARKET_PUSH)
    return MarketEnvelope(
        kind=kind,
        ingress_session_id=str(rec.get("ingress_session_id") or ""),
        sequence=seq,
        event_time=str(rec.get("event_time") or ""),
        received_at=str(rec.get("received_at") or ""),
        persisted_at=str(rec.get("persisted_at") or ""),
        published_at="",
        symbol=str(rec.get("symbol") or payload.get("Symbol") or payload.get("symbol") or ""),
        payload=dict(payload),
        connection_generation=int(rec.get("connection_generation") or 0),
        registration_generation=int(rec.get("registration_generation") or 0),
        capture_part=part_name or str(rec.get("capture_part") or ""),
        raw_record_id=str(rec.get("raw_record_id") or ""),
        entry_blocked=bool(rec.get("entry_blocked")),
        entry_block_reason=str(rec.get("entry_block_reason") or ""),
        meta=dict(rec.get("meta") or {}),
    )


def _first_sequence_in_part(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                    seq = int(rec.get("sequence") or 0)
                except Exception:
                    continue
                if seq > 0:
                    return seq
    except Exception:
        return 0
    return 0


class CaptureSequenceReader:
    """Forward-only JSONL cursor over push_part_*.jsonl in one session directory."""

    def __init__(self, capture_dir: Path | str) -> None:
        self.capture_dir = Path(capture_dir)
        self._fh: Any = None
        self._file_i = 0
        self._files: list[Path] = []
        self._last_seq = 0
        self._buf_pos = 0
        self.invalidated = False
        self.aborted = False
        self.records_scanned = 0
        self.last_lookup_status = ""
        self.generation = 0

    @property
    def last_seq(self) -> int:
        return int(self._last_seq)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def reset(self) -> None:
        """CONTINUE restart from file 0. Must not be used to recover a REALTIME head."""
        self.close()
        self._file_i = 0
        self._files = []
        self._last_seq = 0
        self._buf_pos = 0
        self.invalidated = False
        self.aborted = False
        self.last_lookup_status = ""

    def invalidate(self) -> None:
        """Drop iterator, file offset, and buffered line. Do not reopen at seq 1."""
        self.close()
        self._file_i = 0
        self._files = []
        self._last_seq = 0
        self._buf_pos = 0
        self.invalidated = True
        self.aborted = False
        self.last_lookup_status = "invalidated"

    def position_part_for_seq(self, sequence: int) -> bool:
        """Open the part whose first seq is the greatest first-seq <= want.

        Skips earlier parts without reading their records. Does not scan from
        file 0. Returns False if no part can bound the target.
        """
        if self.invalidated:
            return False
        want = int(sequence)
        if want <= 0:
            return False
        self.close()
        self._buf_pos = 0
        self._refresh_files()
        if not self._files:
            return False
        chosen = -1
        chosen_first = 0
        for i, path in enumerate(self._files):
            first = _first_sequence_in_part(path)
            if first <= 0:
                continue
            if first <= want:
                chosen = i
                chosen_first = first
            elif chosen >= 0:
                break
        if chosen < 0:
            return False
        self._file_i = chosen
        self._last_seq = max(0, chosen_first - 1)
        self._buf_pos = 0
        return True

    def get(
        self,
        sequence: int,
        *,
        abort_check: Optional[AbortCheck] = None,
        max_scan_records: Optional[int] = None,
    ) -> Optional[MarketEnvelope]:
        want = int(sequence)
        if want <= 0:
            self.last_lookup_status = "invalid_want"
            return None
        if self.invalidated:
            self.last_lookup_status = "invalidated"
            return None
        if abort_check is not None and abort_check():
            self.aborted = True
            self.last_lookup_status = "aborted"
            return None
        if self._last_seq >= want:
            self.reset()
        scanned = 0
        while True:
            if abort_check is not None and abort_check():
                self.aborted = True
                self.last_lookup_status = "aborted"
                return None
            rec, part = self._read_next_record()
            if rec is None:
                self.last_lookup_status = "eof"
                return None
            scanned += 1
            self.records_scanned += 1
            try:
                got = int(rec.get("sequence") or 0)
            except Exception:
                continue
            if got <= 0:
                continue
            self._last_seq = got
            if got == want:
                self.last_lookup_status = "ok"
                return envelope_from_raw_record(rec, part_name=part)
            if got > want:
                self.last_lookup_status = "passed_want"
                return None
            if max_scan_records is not None and scanned >= int(max_scan_records):
                self.last_lookup_status = "chunk_limit"
                return None

    def _refresh_files(self) -> None:
        self._files = sorted(self.capture_dir.glob("push_part_*.jsonl"))

    def _read_next_record(self) -> tuple[Optional[dict[str, Any]], str]:
        while True:
            if self._fh is None:
                self._refresh_files()
                if self._file_i >= len(self._files):
                    return None, ""
                path = self._files[self._file_i]
                try:
                    self._fh = path.open("r", encoding="utf-8", errors="replace")
                    if self._buf_pos:
                        self._fh.seek(self._buf_pos)
                except Exception:
                    return None, ""
            assert self._fh is not None
            pos = self._fh.tell()
            line = self._fh.readline()
            if not line:
                self._fh.close()
                self._fh = None
                self._buf_pos = 0
                prev_n = len(self._files)
                self._refresh_files()
                if self._file_i + 1 < len(self._files):
                    self._file_i += 1
                    continue
                if len(self._files) == prev_n:
                    return None, ""
                continue
            if not line.endswith("\n"):
                try:
                    self._fh.seek(pos)
                    self._buf_pos = pos
                except Exception:
                    pass
                return None, ""
            self._buf_pos = self._fh.tell()
            raw = line.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            part = ""
            if self._file_i < len(self._files):
                part = self._files[self._file_i].name
            return rec, part

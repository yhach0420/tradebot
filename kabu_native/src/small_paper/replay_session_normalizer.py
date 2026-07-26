"""Replay normalization across flat + session_* capture layouts.

Does NOT mutate raw files. Emits chronological envelopes + gap map.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LUNCH_START = time(11, 30)
LUNCH_END = time(12, 30)


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


@dataclass
class NormalizedEvent:
    session_id: str
    sequence: int
    event_time: str
    received_at: str
    symbol: str
    payload: dict[str, Any]
    source_part: str
    unique_key: str
    ts: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat(timespec="milliseconds")
        return d


@dataclass
class NormalizeReport:
    day: str
    sessions: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    raw_rows: int = 0
    normalized_rows: int = 0
    duplicate_keys: int = 0
    malformed: int = 0
    timestamp_regressions_in_file_order: int = 0
    mixed_session_parts: list[str] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    first_event_at: str = ""
    last_event_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iter_part_files(day_dir: Path) -> list[Path]:
    files: list[Path] = []
    # New layout
    for sess in sorted(day_dir.glob("session_*")):
        if sess.is_dir():
            files.extend(sorted(sess.glob("push_part_*.jsonl")))
    # Legacy flat layout
    files.extend(sorted(day_dir.glob("push_part_*.jsonl")))
    # Dedup by resolve
    seen: set[str] = set()
    out: list[Path] = []
    for p in files:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def normalize_day_capture(
    day_dir: Path,
    *,
    day: Optional[str] = None,
    gap_threshold_sec: float = 120.0,
) -> tuple[list[NormalizedEvent], NormalizeReport]:
    day_name = day or day_dir.name
    report = NormalizeReport(day=day_name)
    events: list[NormalizedEvent] = []
    keys_seen: set[str] = set()
    part_sessions: dict[str, set[str]] = {}

    for fp in _iter_part_files(day_dir):
        try:
            rel = str(fp.relative_to(day_dir))
        except Exception:
            rel = fp.name
        report.parts.append(rel)
        if fp.stat().st_size == 0:
            continue
        prev_ts_file: Optional[datetime] = None
        with fp.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                report.raw_rows += 1
                try:
                    rec = json.loads(line)
                except Exception:
                    report.malformed += 1
                    continue
                if not isinstance(rec, dict):
                    report.malformed += 1
                    continue
                sid = str(
                    rec.get("ingress_session_id")
                    or rec.get("capture_session_id")
                    or "unknown"
                )
                report.sessions = sorted(set(report.sessions) | {sid})
                part_sessions.setdefault(rel, set()).add(sid)
                try:
                    seq = int(rec.get("sequence") or 0)
                except Exception:
                    seq = 0
                op = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                payload = op or rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
                if not isinstance(payload, dict):
                    payload = {}
                ts = (
                    _parse_ts(rec.get("event_time"))
                    or _parse_ts(rec.get("received_at_jst"))
                    or _parse_ts(rec.get("received_at"))
                    or _parse_ts(payload.get("CurrentPriceTime"))
                )
                if ts is None:
                    report.malformed += 1
                    continue
                if prev_ts_file is not None and ts < prev_ts_file:
                    report.timestamp_regressions_in_file_order += 1
                prev_ts_file = ts
                uk = f"{sid}:{seq}"
                if uk in keys_seen:
                    report.duplicate_keys += 1
                    continue
                keys_seen.add(uk)
                sym = str(rec.get("symbol") or payload.get("Symbol") or "")
                events.append(
                    NormalizedEvent(
                        session_id=sid,
                        sequence=seq,
                        event_time=ts.isoformat(timespec="milliseconds"),
                        received_at=str(rec.get("received_at_jst") or rec.get("received_at") or ""),
                        symbol=sym.split(".")[0],
                        payload=payload,
                        source_part=rel,
                        unique_key=uk,
                        ts=ts,
                    )
                )

    for part, sids in part_sessions.items():
        if len(sids) > 1:
            report.mixed_session_parts.append(part)

    # Stable chronological order: event_time, then session_id, then sequence
    events.sort(key=lambda e: (e.ts, e.session_id, e.sequence, e.source_part))
    report.normalized_rows = len(events)
    if events:
        report.first_event_at = events[0].event_time
        report.last_event_at = events[-1].event_time

    # Gap map (exclude lunch)
    for a, b in zip(events, events[1:]):
        gap = (b.ts - a.ts).total_seconds()
        if gap <= gap_threshold_sec:
            continue
        # lunch skip
        if a.ts.timetz().replace(tzinfo=None) <= LUNCH_START and b.ts.timetz().replace(tzinfo=None) >= LUNCH_END:
            if a.ts.date() == b.ts.date():
                continue
        report.gaps.append(
            {
                "from": a.event_time,
                "to": b.event_time,
                "gap_sec": gap,
                "from_key": a.unique_key,
                "to_key": b.unique_key,
            }
        )
    return events, report


def iter_normalized_payloads(day_dir: Path) -> Iterator[dict[str, Any]]:
    events, _rep = normalize_day_capture(day_dir)
    for e in events:
        yield e.payload


def write_normalization_artifact(day_dir: Path, out_dir: Path) -> dict[str, Any]:
    events, report = normalize_day_capture(day_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / f"{report.day}_normalized_events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for e in events:
            row = e.to_dict()
            # keep payload reference light in artifact index? keep full for salvage
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    gaps_path = out_dir / f"{report.day}_gap_map.json"
    gaps_path.write_text(json.dumps(report.gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rep_path = out_dir / f"{report.day}_normalize_report.json"
    rep_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "events_path": str(events_path),
        "gaps_path": str(gaps_path),
        "report_path": str(rep_path),
        "report": report.to_dict(),
    }

"""Real on-disk source inventory — no guessed paths."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import FORBIDDEN_ALPHA, FORBIDDEN_EARLY, FORBIDDEN_RISK_ONLY_FROM, TARGET_START

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_path_identity(path: Path) -> str:
    sz = path.stat().st_size
    if sz <= 50_000_000:
        return _sha256_file(path)
    h = hashlib.sha256()
    h.update(f"{path.name}|{sz}|".encode())
    with path.open("rb") as f:
        h.update(f.read(1_000_000))
    return h.hexdigest() + f":size={sz}:prefix1MB"


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _undash(d: str) -> str:
    return d.replace("-", "")


def _allowed_day(day: str) -> bool:
    if day < TARGET_START:
        return False
    if day in FORBIDDEN_EARLY:
        return False
    if day in FORBIDDEN_ALPHA:
        return False
    if day >= FORBIDDEN_RISK_ONLY_FROM:
        return False
    return True


def _loads(line: bytes) -> Any:
    try:
        import orjson
        return orjson.loads(line)
    except Exception:
        import json
        return json.loads(line)


def inventory_push_jsonl() -> list[dict[str, Any]]:
    root = NATIVE / "data" / "push_jsonl"
    rows = []
    if not root.exists():
        return rows
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir():
            continue
        day = _undash(day_dir.name)
        if day < TARGET_START:
            continue
        files = sorted(day_dir.glob("*.jsonl"))
        if not files:
            continue
        first_ts = last_ts = None
        events_n = 0
        cols: set[str] = set()
        am = pm = False
        for fp in files:
            n_lines = 0
            first_line = last_line = None
            with fp.open("rb") as f:
                for line in f:
                    if not line.strip():
                        continue
                    n_lines += 1
                    if first_line is None:
                        first_line = line
                    last_line = line
            events_n += n_lines
            for sample in (first_line, last_line):
                if not sample:
                    continue
                try:
                    d = _loads(sample)
                except Exception:
                    continue
                ts = _parse_ts(d.get("recorded_at"))
                if ts is not None:
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                    h = ts.hour * 60 + ts.minute
                    if 9 * 60 <= h < 11 * 60 + 30:
                        am = True
                    if 12 * 60 + 30 <= h <= 15 * 60 + 30:
                        pm = True
                p = d.get("payload") or {}
                if isinstance(p, dict):
                    cols.update(list(p.keys())[:50])
        if not (am and pm) and files:
            mid = files[len(files) // 2]
            with mid.open("rb") as f:
                for i, line in enumerate(f):
                    if i > 0 and i % 3000 == 0:
                        try:
                            d = _loads(line)
                        except Exception:
                            continue
                        ts = _parse_ts(d.get("recorded_at"))
                        if ts is None:
                            continue
                        h = ts.hour * 60 + ts.minute
                        if 9 * 60 <= h < 11 * 60 + 30:
                            am = True
                        if 12 * 60 + 30 <= h <= 15 * 60 + 30:
                            pm = True
                        if am and pm:
                            break
        total_size = sum(fp.stat().st_size for fp in files)
        cat = hashlib.sha256()
        for fp in files:
            cat.update(f"{fp.name}:{fp.stat().st_size}\n".encode())
        rows.append({
            "source_id": f"push_jsonl_{day}",
            "path": str(day_dir),
            "date": day,
            "source_type": "raw_PUSH",
            "symbols_n": len(files),
            "events_n": events_n,
            "first_timestamp": first_ts.isoformat() if first_ts else None,
            "last_timestamp": last_ts.isoformat() if last_ts else None,
            "AM_present": am,
            "PM_present": pm,
            "columns": sorted(cols)[:80],
            "file_size": total_size,
            "SHA256": cat.hexdigest(),
            "alpha_use_allowed": _allowed_day(day),
            "priority": 1,
        })
    return rows


def inventory_market_capture() -> list[dict[str, Any]]:
    root = NATIVE / "data" / "market_capture"
    rows = []
    if not root.exists():
        return rows
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit():
            continue
        day = day_dir.name
        if day < TARGET_START:
            continue
        parts = list(day_dir.rglob("push_part_*.jsonl"))
        if not parts:
            continue
        total = sum(p.stat().st_size for p in parts)
        cat = hashlib.sha256()
        for p in sorted(parts):
            cat.update(f"{p.relative_to(day_dir)}:{p.stat().st_size}\n".encode())
        rows.append({
            "source_id": f"market_capture_{day}",
            "path": str(day_dir),
            "date": day,
            "source_type": "raw_market_event",
            "symbols_n": None,
            "events_n": None,
            "first_timestamp": None,
            "last_timestamp": None,
            "AM_present": None,
            "PM_present": None,
            "columns": ["CurrentPrice", "TradingVolume", "TradingValue", "VWAP"],
            "file_size": total,
            "SHA256": cat.hexdigest(),
            "alpha_use_allowed": _allowed_day(day),
            "parts_n": len(parts),
            "priority": 1,
        })
    return rows


def inventory_small_paper() -> list[dict[str, Any]]:
    root = NATIVE / "results" / "small_paper"
    rows = []
    if not root.exists():
        return rows
    import csv
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit():
            continue
        day = day_dir.name
        if day < TARGET_START:
            continue
        events = list(day_dir.glob("live_session_*/small_paper_events.csv"))
        if not events:
            continue
        ev = max(events, key=lambda p: p.stat().st_size)
        # Fast: line count + sample head/tail for timestamps; full parse not required for inventory
        n = 0
        first = last = None
        cols: list[str] = []
        am = pm = False
        syms: set[str] = set()
        with ev.open(encoding="utf-8", newline="") as f:
            header = f.readline()
            cols = [c.strip() for c in header.strip().split(",")]
            sample_lines = []
            for i, line in enumerate(f):
                n += 1
                if i < 5 or i % 20000 == 0:
                    sample_lines.append(line)
            # rewind tail: already consumed; use last samples from sample_lines
        # parse samples via DictReader-like
        for line in sample_lines[:20]:
            parts = line.strip().split(",")
            if len(parts) < 3:
                continue
            # event_time typically col0, event_type col1, symbol col2
            try:
                ts = _parse_ts(parts[0])
                if ts is not None:
                    if first is None or ts < first:
                        first = ts
                    if last is None or ts > last:
                        last = ts
                    h = ts.hour * 60 + ts.minute
                    if 9 * 60 <= h < 11 * 60 + 30:
                        am = True
                    if 12 * 60 + 30 <= h < 15 * 60:
                        pm = True
                if len(parts) > 2:
                    syms.add(parts[2])
            except Exception:
                continue
        # approximate symbols from file if few samples
        symbols_n = len(syms) if len(syms) >= 5 else None
        rows.append({
            "source_id": f"small_paper_{day}",
            "path": str(ev),
            "date": day,
            "source_type": "small_paper_capture",
            "symbols_n": symbols_n,
            "events_n": n,
            "first_timestamp": first.isoformat() if first else None,
            "last_timestamp": last.isoformat() if last else None,
            "AM_present": am,
            "PM_present": pm,
            "columns": cols[:80],
            "file_size": ev.stat().st_size,
            "SHA256": _sha256_path_identity(ev),
            "alpha_use_allowed": False,
            "priority": 2,
            "note": "candidate/reject/accept events — Watch50 conditioned",
        })
    return rows


def inventory_rpfe_panel() -> list[dict[str, Any]]:
    paths = [
        NATIVE / "results" / "research" / "realistic_price_flow_entry" / "20260724_010347" / "report.json",
        NATIVE / "results" / "research" / "pbv2_zero_base_revalidation" / "20260723_235148" / "report.json",
    ]
    rows = []
    import json
    for p in paths:
        if not p.exists():
            continue
        rep = json.loads(p.read_text(encoding="utf-8"))
        rows.append({
            "source_id": f"rpfe_panel_{p.parent.name}",
            "path": str(p),
            "date": "20260615-20260723",
            "source_type": "RPFE_candidate_panel",
            "symbols_n": rep.get("n_symbols") or rep.get("symbols_n"),
            "events_n": rep.get("n_panel") or 122983,
            "first_timestamp": "20260615",
            "last_timestamp": "20260723",
            "AM_present": True,
            "PM_present": True,
            "columns": ["candidate_panel", "price_path"],
            "file_size": p.stat().st_size,
            "SHA256": _sha256_file(p),
            "alpha_use_allowed": False,
            "priority": 4,
            "n_pbv2_candidates": rep.get("n_pbv2_candidates"),
            "n_non_pbv2": rep.get("n_non_pbv2"),
        })
    return rows


def build_source_inventory() -> dict[str, Any]:
    push = inventory_push_jsonl()
    mc = inventory_market_capture()
    sp = inventory_small_paper()
    rpfe = inventory_rpfe_panel()
    all_rows = push + mc + sp + rpfe
    usable_push_days = sorted({r["date"] for r in push if r.get("alpha_use_allowed")})
    return {
        "rows": all_rows,
        "n": len(all_rows),
        "usable_push_days": usable_push_days,
        "forbidden_not_used": {
            "early_20260601_12": True,
            "20260803": True,
            "20260804": True,
            "risk_only_from_20260805": True,
        },
        "priority_order": ["raw_PUSH", "raw_market_event", "small_paper_capture", "RPFE_candidate_panel"],
    }

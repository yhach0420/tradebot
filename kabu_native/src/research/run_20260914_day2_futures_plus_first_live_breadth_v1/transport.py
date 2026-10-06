"""First-live ranking transport proof. No reconstruction. No strategy."""
from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.market_breadth_leadership_acquisition_v1 import RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.writer import ranking_path
from research.new_causal_information_acquisition_v1.completeness import parse_received_at
from research.run_20260914_day2_futures_plus_first_live_breadth_v1.isolation import NATIVE

JST = ZoneInfo("Asia/Tokyo")
WINDOW_START = time(9, 5)
WINDOW_END = time(11, 25)
COVERAGE_FIRST = time(9, 10)
COVERAGE_LAST = time(11, 20)


def _pid_alive(pid: int) -> bool:
    n = int(pid or 0)
    if n <= 0:
        return False
    try:
        os.kill(n, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _ranking_items(raw: Any) -> Optional[list[Any]]:
    if not isinstance(raw, dict):
        return None
    ranking = raw.get("Ranking")
    if ranking is None:
        return None
    if isinstance(ranking, list):
        return ranking
    return None


def summarize_type(day: str, typ: int, *, native_root: Optional[Path] = None) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    path = root / "data" / "market_breadth_capture" / str(day) / f"ranking_type{int(typ)}.jsonl"
    if native_root is None:
        path = ranking_path(day, typ)
    rows = _iter_jsonl(path)
    times: list[datetime] = []
    empty_n = 0
    http_429_n = 0
    http_error_n = 0
    schema_drift_n = 0
    snapshot_n = 0
    prev_t: Optional[datetime] = None
    reverse_n = 0
    for rec in rows:
        snapshot_n += 1
        dt = parse_received_at(rec.get("received_at"))
        if dt is not None:
            times.append(dt)
            if prev_t is not None and dt < prev_t:
                reverse_n += 1
            prev_t = dt
        status = rec.get("http_status")
        if status == 429:
            http_429_n += 1
        elif status != 200:
            http_error_n += 1
        req = rec.get("requested_type")
        if req is not None and int(req) != int(typ):
            schema_drift_n += 1
        div = rec.get("ExchangeDivision")
        if div not in (None, "", "T"):
            schema_drift_n += 1
        raw = rec.get("raw")
        items = _ranking_items(raw)
        if status == 200:
            if items is None:
                schema_drift_n += 1
            elif len(items) == 0:
                empty_n += 1
        elif raw is not None and not isinstance(raw, (dict, str)):
            schema_drift_n += 1
    times.sort()
    empty_rate = (float(empty_n) / float(snapshot_n)) if snapshot_n else None
    return {
        "type": int(typ),
        "path": str(path),
        "present": path.is_file(),
        "snapshot_n": snapshot_n,
        "empty_n": empty_n,
        "empty_rate": empty_rate,
        "http_429_n": http_429_n,
        "http_error_n": http_error_n,
        "schema_drift_n": schema_drift_n,
        "received_at_reverse_n": reverse_n,
        "received_at_advancing": bool(snapshot_n > 0 and reverse_n == 0),
        "first_received_at": times[0].isoformat() if times else None,
        "last_received_at": times[-1].isoformat() if times else None,
        "times": times,
    }


def _in_window(dt: datetime) -> bool:
    t = dt.astimezone(JST).time()
    return WINDOW_START <= t <= WINDOW_END


def evaluate_transport(*, native_root: Optional[Path] = None, trading_date: str) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    day = str(trading_date)
    dest = root / "data" / "market_breadth_capture" / day
    by_type: dict[int, dict[str, Any]] = {}
    for typ in RANKING_TYPES:
        by_type[int(typ)] = summarize_type(day, int(typ), native_root=root)
    seven = all(bool(by_type[t]["present"]) and int(by_type[t]["snapshot_n"] or 0) > 0 for t in RANKING_TYPES)
    snapshot_n_by_type = {str(t): by_type[t]["snapshot_n"] for t in RANKING_TYPES}
    empty_n = sum(int(by_type[t]["empty_n"] or 0) for t in RANKING_TYPES)
    snap_n = sum(int(by_type[t]["snapshot_n"] or 0) for t in RANKING_TYPES)
    empty_rate = (float(empty_n) / float(snap_n)) if snap_n else None
    http_429_n = sum(int(by_type[t]["http_429_n"] or 0) for t in RANKING_TYPES)
    http_error_n = sum(int(by_type[t]["http_error_n"] or 0) for t in RANKING_TYPES)
    schema_drift_n = sum(int(by_type[t]["schema_drift_n"] or 0) for t in RANKING_TYPES)
    advancing = all(bool(by_type[t]["received_at_advancing"]) for t in RANKING_TYPES) if seven else False
    firsts = [parse_received_at(by_type[t]["first_received_at"]) for t in RANKING_TYPES]
    lasts = [parse_received_at(by_type[t]["last_received_at"]) for t in RANKING_TYPES]
    firsts_ok = [t for t in firsts if t is not None]
    lasts_ok = [t for t in lasts if t is not None]
    coverage = False
    if firsts_ok and lasts_ok:
        earliest = min(firsts_ok)
        latest = max(lasts_ok)
        coverage = earliest.astimezone(JST).time() <= COVERAGE_FIRST and latest.astimezone(JST).time() >= COVERAGE_LAST
        window_hits = []
        for t in RANKING_TYPES:
            n_win = sum(1 for x in by_type[t].get("times") or [] if _in_window(x))
            window_hits.append(n_win)
        min_window_n = min(window_hits) if window_hits else 0
    else:
        earliest = None
        latest = None
        min_window_n = 0
    pid_path = dest / "breadth_collector.pid"
    exit_path = dest / "breadth_collector_exit.json"
    pid = 0
    if pid_path.is_file():
        try:
            pid = int((pid_path.read_text(encoding="utf-8").strip() or "0").split()[0])
        except ValueError:
            pid = 0
    exit_body: dict[str, Any] = {}
    if exit_path.is_file():
        try:
            exit_body = json.loads(exit_path.read_text(encoding="utf-8"))
        except Exception:
            exit_body = {}
    alive = _pid_alive(pid)
    clean_exit = bool(exit_body.get("clean_exit")) and not alive
    full = bool(
        seven
        and advancing
        and schema_drift_n == 0
        and coverage
        and min_window_n >= 2
        and clean_exit
        and empty_rate is not None
        and empty_rate < 1.0
    )
    slim = {
        str(t): {k: v for k, v in by_type[t].items() if k != "times"}
        for t in RANKING_TYPES
    }
    return {
        "day": day,
        "FULL": full,
        "seven_types_present": seven,
        "snapshot_n_by_type": snapshot_n_by_type,
        "empty_n": empty_n,
        "empty_rate": empty_rate,
        "http_429_n": http_429_n,
        "http_error_n": http_error_n,
        "schema_drift_n": schema_drift_n,
        "received_at_advancing": advancing,
        "first_received_at": earliest.isoformat() if earliest else None,
        "last_received_at": latest.isoformat() if latest else None,
        "coverage_0905_1125": coverage,
        "min_window_snapshot_n": min_window_n,
        "collector_pid": pid,
        "collector_pid_alive": alive,
        "collector_clean_exit": clean_exit,
        "exit": {k: exit_body.get(k) for k in ("pid", "exit_at", "cycles", "clean_exit", "error", "http_429_n")},
        "by_type": slim,
        "raw_dir": str(dest),
    }


def load_type_records(day: str, typ: int, *, native_root: Optional[Path] = None) -> list[dict[str, Any]]:
    root = Path(native_root) if native_root else NATIVE
    path = root / "data" / "market_breadth_capture" / str(day) / f"ranking_type{int(typ)}.jsonl"
    rows = []
    for rec in _iter_jsonl(path):
        dt = parse_received_at(rec.get("received_at"))
        if dt is None:
            continue
        rows.append(rec)
    rows.sort(key=lambda r: parse_received_at(r.get("received_at")) or datetime.min.replace(tzinfo=JST))
    return rows

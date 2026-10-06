"""Live ranking poller. GET only. No /register. No 20260911 backfill. No strategy."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.market_breadth_leadership_acquisition_v1 import (
    DEFAULT_CADENCE_SEC,
    EXCHANGE_DIVISION,
    FALLBACK_CADENCE_SEC,
    PRIMARY_WINDOW_END_HM,
    PRIMARY_WINDOW_START_HM,
    RANKING_TYPES,
)
from research.market_breadth_leadership_acquisition_v1.client import get_ranking, issue_readonly_token
from research.market_breadth_leadership_acquisition_v1.isolation import NATIVE
from research.market_breadth_leadership_acquisition_v1.writer import (
    append_ranking_record,
    day_dir,
    refuse_historical_pseudosync,
)

JST = ZoneInfo("Asia/Tokyo")


def in_primary_window(now: Optional[datetime] = None) -> bool:
    dt = now or datetime.now(JST)
    t = dt.astimezone(JST).time()
    return dtime(*PRIMARY_WINDOW_START_HM) <= t <= dtime(*PRIMARY_WINDOW_END_HM)


def next_cadence(current_sec: int, *, saw_429: bool) -> int:
    """Fail-soft: drop to 120s after any 429. Do not climb back to 60s in-session."""
    if saw_429:
        return int(FALLBACK_CADENCE_SEC)
    return int(current_sec or DEFAULT_CADENCE_SEC)


def write_breadth_pid(day: str, *, pid: Optional[int] = None) -> Path:
    n = int(pid or os.getpid())
    dest_dir = day_dir(day)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "breadth_collector.pid"
    path.write_text(str(n) + "\n", encoding="utf-8")
    meta = dest_dir / "breadth_collector_meta.json"
    meta.write_text(
        json.dumps(
            {
                "pid": n,
                "trading_date": str(day),
                "started_at": datetime.now(JST).isoformat(timespec="seconds"),
                "command": "python kabu_native\\scripts\\run_market_breadth_leadership_capture.py --live",
                "register_mutation_n": 0,
                "unregister_n": 0,
                "sendorder_n": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_breadth_exit(day: str, body: dict[str, Any]) -> Path:
    dest_dir = day_dir(day)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "breadth_collector_exit.json"
    payload = {
        "pid": body.get("pid"),
        "trading_date": str(day),
        "exit_at": datetime.now(JST).isoformat(timespec="seconds"),
        "cycles": body.get("cycles"),
        "http_429_n": body.get("http_429_n"),
        "cadence_sec": body.get("cadence_sec"),
        "clean_exit": bool(body.get("clean_exit")),
        "error": body.get("error"),
        "register_mutation_n": 0,
        "unregister_n": 0,
        "sendorder_n": 0,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_live_poll(
    *,
    native_root: Optional[Path] = None,
    trading_date: str,
    cadence_sec: int = DEFAULT_CADENCE_SEC,
    now: Optional[datetime] = None,
    once: bool = False,
) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    clock = now or datetime.now(JST)
    refuse_historical_pseudosync(trading_date, now=clock)
    if not in_primary_window(clock):
        raise ValueError("live ranking capture is 09:05-11:25 JST only")
    pid_path = write_breadth_pid(trading_date)
    exit_body: dict[str, Any] = {
        "pid": os.getpid(),
        "cycles": 0,
        "http_429_n": 0,
        "cadence_sec": int(cadence_sec or DEFAULT_CADENCE_SEC),
        "clean_exit": False,
    }
    try:
        rest, token = issue_readonly_token(native_root=root)
        cycles = 0
        http_429_n = 0
        cadence = int(cadence_sec or DEFAULT_CADENCE_SEC)
        while True:
            cycle_clock = datetime.now(JST)
            if not in_primary_window(cycle_clock):
                break
            saw_429 = False
            for typ in RANKING_TYPES:
                rec = get_ranking(rest=rest, token=token, ranking_type=typ, exchange_division=EXCHANGE_DIVISION)
                append_ranking_record(trading_date, rec)
                if rec.get("http_status") == 429:
                    http_429_n += 1
                    saw_429 = True
            cadence = next_cadence(cadence, saw_429=saw_429)
            cycles += 1
            if once:
                break
            time.sleep(max(1, int(cadence)))
        result = {
            "cycles": cycles,
            "http_429_n": http_429_n,
            "cadence_sec": cadence,
            "pid": os.getpid(),
            "pid_path": str(pid_path),
            "clean_exit": True,
        }
        exit_body.update(result)
        exit_body["clean_exit"] = True
        return result
    except Exception as exc:
        exit_body["error"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        write_breadth_exit(trading_date, exit_body)

"""Locate native 1m for a session. Does not invent Bid/Ask. Does not scrape."""
from __future__ import annotations

from typing import Any

import pandas as pd

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.pb1_v4_prospective_semantic_validation.isolation import (
    CAPTURE_ROOT,
    CONTEXT_CAPTURE_ROOT,
    INTRADAY_ROOT,
    PARQUET_MINUTE,
)


def session_dirs(session: str) -> dict[str, Any]:
    day = str(session).replace("-", "")[:8]
    iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    return {
        "session": day,
        "parquet_minute_dir": str(PARQUET_MINUTE),
        "capture": str(CAPTURE_ROOT / day),
        "context_capture": str(CONTEXT_CAPTURE_ROOT / day),
        "intraday_csv": str(INTRADAY_ROOT / day),
        "intraday_csv_iso": str(INTRADAY_ROOT / iso),
        "capture_exists": (CAPTURE_ROOT / day).is_dir(),
        "context_exists": (CONTEXT_CAPTURE_ROOT / day).is_dir(),
        "intraday_exists": (INTRADAY_ROOT / day).is_dir() or (INTRADAY_ROOT / iso).is_dir(),
    }


def parquet_panel_bounds() -> dict[str, Any]:
    if not PARQUET_MINUTE.is_dir():
        return {"ok": False, "min_date": None, "max_date": None}
    files = sorted(PARQUET_MINUTE.glob("minute_*.parquet"))
    if not files:
        return {"ok": False, "min_date": None, "max_date": None}
    df = pd.read_parquet(files[0], columns=["date"])
    dates = [str(x) for x in df["date"].tolist()]
    return {"ok": True, "min_date": min(dates) if dates else None, "max_date": max(dates) if dates else None, "n_dates_sample": len(set(dates))}


def parquet_has_date(session: str, *, symbols: list[str]) -> dict[str, Any]:
    day = str(session).replace("-", "")[:8]
    bounds = parquet_panel_bounds()
    if not bounds.get("ok"):
        return {"ok": False, "row_n": 0, "reason": "parquet_dir_missing", **bounds}
    lo, hi = str(bounds.get("min_date") or ""), str(bounds.get("max_date") or "")
    if not lo or day < lo or day > hi:
        return {"ok": False, "row_n": 0, "reason": "session_outside_historical_parquet", **bounds}
    _ = symbols
    minutes = load_minutes(symbols=list(symbols), allowed_dates={day}, forbidden_dates=set())
    n = 0 if minutes is None or minutes.empty else int(len(minutes))
    return {"ok": n > 0, "row_n": n, "reason": None if n > 0 else "empty_after_filter", **bounds}


def collect_session(session: str, *, symbols: list[str]) -> dict[str, Any]:
    """Return native minutes for `session` if present. Never fetches missing days."""
    day = str(session).replace("-", "")[:8]
    loc = session_dirs(day)
    bounds = parquet_panel_bounds()
    in_panel = bool(bounds.get("ok") and bounds.get("min_date") and str(bounds["min_date"]) <= day <= str(bounds["max_date"]))
    pq: dict[str, Any]
    minutes = None
    source = None
    if in_panel:
        pq = parquet_has_date(day, symbols=symbols)
        if pq.get("ok"):
            minutes = load_minutes(symbols=list(symbols), allowed_dates={day}, forbidden_dates=set())
            source = "historical_parquet"
    else:
        pq = {"ok": False, "row_n": 0, "reason": "session_outside_historical_parquet", **bounds}
    present = bool((minutes is not None and not minutes.empty) or loc["capture_exists"] or loc["context_exists"] or loc["intraday_exists"])
    # Capture dirs without reconstructed native 1m bars are not yet a complete session.
    complete_source = minutes is not None and not minutes.empty
    return {
        "session": day,
        "locations": loc,
        "parquet": pq,
        "present_any_capture_dir": present,
        "native_1m_ready": bool(complete_source),
        "source": source,
        "minutes_row_n": 0 if minutes is None or getattr(minutes, "empty", True) else int(len(minutes)),
        "minutes": minutes,
        "reason": None if complete_source else "native_1m_not_on_disk",
    }

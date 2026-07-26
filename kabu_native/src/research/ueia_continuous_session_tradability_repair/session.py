"""TSE cash session classification — sourced from market_capture_sidecar."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional

from small_paper.market_capture_sidecar import is_market_session_jst

# Explicit boundaries matching is_market_session_jst
AM_OPEN = time(9, 0)
AM_CLOSE = time(11, 30)
PM_OPEN = time(12, 30)
PM_CLOSE = time(15, 30)


def classify_session(ts: datetime) -> str:
    t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
    if t < AM_OPEN:
        return "PREOPEN"
    if AM_OPEN <= t < AM_CLOSE:
        return "CONTINUOUS_AM"
    if AM_CLOSE <= t < PM_OPEN:
        return "LUNCH_BREAK"
    if PM_OPEN <= t < PM_CLOSE:
        return "CONTINUOUS_PM"
    if t >= PM_CLOSE:
        return "AFTER_MARKET"
    return "UNKNOWN_SESSION"


def market_tradable(ts: datetime) -> bool:
    return bool(is_market_session_jst(ts))


def continuous_session_id(ts: datetime) -> Optional[str]:
    st = classify_session(ts)
    if st == "CONTINUOUS_AM":
        return "AM"
    if st == "CONTINUOUS_PM":
        return "PM"
    return None


def session_end_time(ts: datetime) -> Optional[datetime]:
    st = classify_session(ts)
    d = ts.date()
    tz = ts.tzinfo
    if st == "CONTINUOUS_AM":
        return datetime.combine(d, AM_CLOSE, tzinfo=tz)
    if st == "CONTINUOUS_PM":
        return datetime.combine(d, PM_CLOSE, tzinfo=tz)
    return None


def seconds_since_session_open(ts: datetime) -> Optional[float]:
    st = classify_session(ts)
    d = ts.date()
    tz = ts.tzinfo
    if st == "CONTINUOUS_AM":
        open_t = datetime.combine(d, AM_OPEN, tzinfo=tz)
        return (ts - open_t).total_seconds()
    if st == "CONTINUOUS_PM":
        open_t = datetime.combine(d, PM_OPEN, tzinfo=tz)
        return (ts - open_t).total_seconds()
    return None


def seconds_to_session_end(ts: datetime) -> Optional[float]:
    end = session_end_time(ts)
    if end is None:
        return None
    return (end - ts).total_seconds()


def crosses_boundary(t0: datetime, t1: datetime) -> bool:
    """True if path from t0 to t1 leaves the continuous session of t0."""
    s0 = continuous_session_id(t0)
    if s0 is None:
        return True
    s1 = continuous_session_id(t1)
    return s1 != s0

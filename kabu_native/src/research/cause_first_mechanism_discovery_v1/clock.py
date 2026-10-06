"""Causal clocks. BAR_START bar [T, T+1m) usable at T+1m. No same-bar close ENTRY."""
from __future__ import annotations

from datetime import datetime, timedelta


def parse_hhmm(value: str) -> tuple[int, int] | None:
    parts = str(value or "").split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def hhmm_add(value: str, minutes: int) -> str | None:
    parsed = parse_hhmm(value)
    if parsed is None:
        return None
    dt = datetime(2000, 1, 1, parsed[0], parsed[1]) + timedelta(minutes=int(minutes))
    return dt.strftime("%H:%M")


def in_lunch(hhmm: str) -> bool:
    parsed = parse_hhmm(hhmm)
    if parsed is None:
        return False
    m = parsed[0] * 60 + parsed[1]
    return 11 * 60 + 30 <= m < 12 * 60 + 30


def interval_crosses_lunch(start: str, end: str) -> bool:
    a = parse_hhmm(start)
    b = parse_hhmm(end)
    if a is None or b is None:
        return True
    sm = a[0] * 60 + a[1]
    em = b[0] * 60 + b[1]
    lunch0 = 11 * 60 + 30
    lunch1 = 12 * 60 + 30
    return sm < lunch0 < em or sm < lunch1 <= em or (sm >= lunch0 and sm < lunch1)


assert hhmm_add("09:30", 1) == "09:31"
assert hhmm_add("09:30", 15) == "09:45"
assert interval_crosses_lunch("11:20", "11:40") is True
assert interval_crosses_lunch("09:30", "09:45") is False
assert in_lunch("12:00") is True

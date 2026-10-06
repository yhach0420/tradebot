"""TSE eligible-day calendar for Japan-session coverage audit only. Not an alpha window."""
from __future__ import annotations

from datetime import date, timedelta

from research.causal_driver_pb1 import C1_LAST, DEV_FIRST
from research.causal_driver_pb1.phase1.dates import parse_yyyymmdd, yyyymmdd

# Public JPX holidays + year-end closures covering Development through C1.
# Not loaded from stock 1m panels. Not imported from prospective packages.
JPX_CLOSED_WEEKDAYS = frozenset(
    {
        "20240923",
        "20241014",
        "20241104",
        "20241231",
        "20250101",
        "20250102",
        "20250103",
        "20250113",
        "20250211",
        "20250224",
        "20250320",
        "20250429",
        "20250503",
        "20250504",
        "20250505",
        "20250506",
        "20250721",
        "20250811",
        "20250915",
        "20250923",
        "20251013",
        "20251103",
        "20251124",
        "20251231",
        "20260101",
        "20260102",
        "20260112",
        "20260211",
        "20260223",
        "20260320",
    }
)


def is_tse_eligible_day(d: date | str) -> bool:
    day = parse_yyyymmdd(yyyymmdd(d)) if not isinstance(d, date) else d
    if day.weekday() >= 5:
        return False
    return yyyymmdd(day) not in JPX_CLOSED_WEEKDAYS


def tse_eligible_days(first: str = DEV_FIRST, last: str = C1_LAST) -> list[str]:
    a = parse_yyyymmdd(first)
    b = parse_yyyymmdd(last)
    out: list[str] = []
    cur = a
    while cur <= b:
        if is_tse_eligible_day(cur):
            out.append(yyyymmdd(cur))
        cur += timedelta(days=1)
    return out

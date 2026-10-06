"""Japan cash-session USDJPY coverage audit. Not an alpha window. Lunch is not dropped."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.phase1 import JAPAN_SESSION_END, JAPAN_SESSION_EXPECTED_N, JAPAN_SESSION_START
from research.causal_driver_pb1.phase1.bars import UsdJpyNormalizedBar
from research.causal_driver_pb1.phase1.calendar import tse_eligible_days


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def japan_session_minutes(day: str) -> list[str]:
    y = int(day[0:4])
    m = int(day[4:6])
    d = int(day[6:8])
    start = datetime(y, m, d, JAPAN_SESSION_START[0], JAPAN_SESSION_START[1], 0, tzinfo=JST)
    end = datetime(y, m, d, JAPAN_SESSION_END[0], JAPAN_SESSION_END[1], 0, tzinfo=JST)
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(_hhmm(cur))
        cur += timedelta(minutes=1)
    return out


def japan_session_coverage(bars: list[UsdJpyNormalizedBar]) -> dict[str, Any]:
    expected_times = japan_session_minutes("20240917")
    if len(expected_times) != JAPAN_SESSION_EXPECTED_N:
        raise RuntimeError(f"japan_session_expected_n_mismatch:{len(expected_times)}")
    by_date: dict[str, set[str]] = {}
    for bar in bars:
        if bar.quality_status != QualityStatus.VALID:
            continue
        hhmm = _hhmm(bar.bar_start)
        if hhmm < "07:55" or hhmm > "15:30":
            continue
        by_date.setdefault(bar.jst_date, set()).add(hhmm)
    days = tse_eligible_days()
    rows: list[dict[str, Any]] = []
    ratios: list[float] = []
    for day in days:
        actual = by_date.get(day, set())
        actual_n = len(actual)
        missing_n = JAPAN_SESSION_EXPECTED_N - actual_n
        ratio = actual_n / float(JAPAN_SESSION_EXPECTED_N)
        ratios.append(ratio)
        rows.append(
            {
                "date": day,
                "expected_n": JAPAN_SESSION_EXPECTED_N,
                "actual_n": actual_n,
                "missing_n": missing_n,
                "coverage_ratio": ratio,
            }
        )
    if not ratios:
        stats = {"coverage_days": 0, "coverage_min": None, "coverage_median": None, "coverage_p05": None, "days_below_99": 0}
    else:
        ordered = sorted(ratios)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2.0
        p05_i = int(0.05 * (len(ordered) - 1))
        stats = {
            "coverage_days": len(ordered),
            "coverage_min": ordered[0],
            "coverage_median": median,
            "coverage_p05": ordered[p05_i],
            "days_below_99": sum(1 for r in ratios if r < 0.99),
        }
    return {
        **stats,
        "rows": rows,
        "lunch_dropped": False,
        "alpha_window": False,
        "session": "07:55-15:30 JST inclusive",
        "expected_n_per_day": JAPAN_SESSION_EXPECTED_N,
    }

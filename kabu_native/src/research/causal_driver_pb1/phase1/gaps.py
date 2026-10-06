"""Classify FX gaps. Weekend closure is EXPECTED_CLOSED, not UNEXPECTED_MISSING."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.contracts.time import UTC
from research.causal_driver_pb1.phase1.bars import UsdJpyNormalizedBar

NY = ZoneInfo("America/New_York")
ONE_MIN_MS = 60_000


def is_fx_expected_closed(ts_utc: datetime) -> bool:
    """FX weekend: Friday 17:00 America/New_York through Sunday 17:00 exclusive. DST-aware."""
    ny = ts_utc.astimezone(NY)
    wd = ny.weekday()
    close_open = ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if wd == 5:
        return True
    if wd == 6:
        return ny < close_open
    if wd == 4:
        return ny >= close_open
    return False


def classify_missing_minute(ts_utc_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_utc_ms / 1000.0, tz=UTC)
    if is_fx_expected_closed(dt):
        return "EXPECTED_CLOSED"
    return "UNEXPECTED_MISSING"


def classify_gaps(bars: list[UsdJpyNormalizedBar]) -> dict[str, Any]:
    ordered = sorted(
        [b for b in bars if b.quality_status == QualityStatus.VALID],
        key=lambda b: b.ts_utc_ms,
    )
    expected_n = 0
    unexpected_n = 0
    unexpected_missing_minute_n = 0
    expected_closed_minute_n = 0
    unexpected_examples: list[dict[str, Any]] = []
    weekend_examples: list[dict[str, Any]] = []
    prev = None
    for bar in ordered:
        if prev is None:
            prev = bar
            continue
        gap_ms = bar.ts_utc_ms - prev.ts_utc_ms
        if gap_ms <= ONE_MIN_MS:
            prev = bar
            continue
        t = prev.ts_utc_ms + ONE_MIN_MS
        unexp = 0
        exp = 0
        while t < bar.ts_utc_ms:
            label = classify_missing_minute(t)
            if label == "EXPECTED_CLOSED":
                exp += 1
            else:
                unexp += 1
            t += ONE_MIN_MS
        expected_closed_minute_n += exp
        unexpected_missing_minute_n += unexp
        row = {
            "prev_bar_start_jst": prev.bar_start.isoformat(),
            "next_bar_start_jst": bar.bar_start.isoformat(),
            "missing_minutes": (gap_ms // ONE_MIN_MS) - 1,
            "expected_closed_minutes": exp,
            "unexpected_minutes": unexp,
            "class": "EXPECTED_CLOSED" if unexp == 0 else "UNEXPECTED_MISSING",
        }
        if unexp == 0:
            expected_n += 1
            if len(weekend_examples) < 8:
                weekend_examples.append(row)
        else:
            unexpected_n += 1
            if len(unexpected_examples) < 40:
                unexpected_examples.append(row)
        prev = bar
    return {
        "expected_closed_gap_n": expected_n,
        "unexpected_gap_n": unexpected_n,
        "expected_closed_minute_n": expected_closed_minute_n,
        "unexpected_missing_minute_n": unexpected_missing_minute_n,
        "unexpected_examples": unexpected_examples,
        "expected_examples": weekend_examples,
        "forward_fill": False,
        "interpolated": False,
    }

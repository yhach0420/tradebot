"""Equity minute Time = BAR_START or BAR_END. Do not guess. Do not proceed UNKNOWN."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")

SEMANTICS_BAR_START = "BAR_START"
SEMANTICS_BAR_END = "BAR_END"
SEMANTICS_UNKNOWN = "UNKNOWN"


def _hhmm(value: str) -> tuple[int, int] | None:
    s = str(value or "").strip()
    parts = s.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def tick_minute_hhmm(tick_time: str) -> str | None:
    parsed = _hhmm(tick_time)
    if parsed is None:
        return None
    return f"{parsed[0]:02d}:{parsed[1]:02d}"


def previous_hhmm(hhmm: str) -> str | None:
    parsed = _hhmm(hhmm)
    if parsed is None:
        return None
    h, m = parsed
    dt = datetime(2000, 1, 1, h, m) - timedelta(minutes=1)
    return dt.strftime("%H:%M")


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def ohlcv_from_prints(prints: Sequence[dict[str, Any]]) -> dict[str, float] | None:
    prices: list[float] = []
    volume = 0.0
    for rec in prints:
        px = _num(rec.get("price"))
        vo = _num(rec.get("volume")) or 0.0
        if px is None:
            continue
        prices.append(px)
        volume += vo
    if not prices:
        return None
    return {"O": prices[0], "H": max(prices), "L": min(prices), "C": prices[-1], "Vo": volume}


def ohlcv_close_enough(bar: dict[str, Any] | None, bucket: dict[str, float] | None) -> bool:
    if not bar or not bucket:
        return False
    for key in ("O", "H", "L", "C"):
        a = _num(bar.get(key))
        b = _num(bucket.get(key))
        if a is None or b is None:
            return False
        if abs(a - b) > 1e-6:
            return False
    a_vo = _num(bar.get("Vo"))
    b_vo = _num(bucket.get("Vo"))
    if a_vo is not None and b_vo is not None and abs(a_vo - b_vo) > 1e-6:
        return False
    return True


def infer_time_semantics(
    *,
    labeled_bar: str,
    tick_times: Sequence[str],
    bar_ohlcv: dict[str, Any] | None = None,
    ticks_in_label_ohlcv: dict[str, float] | None = None,
    ticks_in_prev_ohlcv: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compare 09:00 minute bar label to tick print minutes. Documentation is not sufficient."""
    label = str(labeled_bar or "").strip()
    prev = previous_hhmm(label)
    in_label = 0
    in_prev = 0
    other = 0
    first_tick = None
    last_tick = None
    for raw in tick_times:
        t = str(raw or "").strip()
        if not t:
            continue
        if first_tick is None:
            first_tick = t
        last_tick = t
        mm = tick_minute_hhmm(t)
        if mm == label:
            in_label += 1
        elif prev and mm == prev:
            in_prev += 1
        else:
            other += 1
    semantics = SEMANTICS_UNKNOWN
    if in_label > 0 and in_prev == 0:
        semantics = SEMANTICS_BAR_START
    elif in_prev > 0 and in_label == 0:
        semantics = SEMANTICS_BAR_END
    label_match = ohlcv_close_enough(bar_ohlcv, ticks_in_label_ohlcv)
    prev_match = ohlcv_close_enough(bar_ohlcv, ticks_in_prev_ohlcv)
    if label_match and not prev_match:
        semantics = SEMANTICS_BAR_START
    elif prev_match and not label_match:
        semantics = SEMANTICS_BAR_END
    elif label_match and prev_match:
        semantics = SEMANTICS_UNKNOWN
    return {
        "EQUITY_MINUTE_TIME_SEMANTICS": semantics,
        "labeled_bar": label,
        "tick_n": in_label + in_prev + other,
        "ticks_in_labeled_minute": in_label,
        "ticks_in_previous_minute": in_prev,
        "ticks_other_minute": other,
        "first_tick_time": first_tick,
        "last_tick_time": last_tick,
        "ohlcv_match_labeled_minute": label_match,
        "ohlcv_match_previous_minute": prev_match,
        "documentation_alone_insufficient": True,
        "did_not_guess": True,
    }


def bar_clock(*, labeled_date: str, labeled_time: str, semantics: str) -> dict[str, Any] | None:
    if semantics not in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
        return None
    day = str(labeled_date).replace("-", "")
    hh, mm = _hhmm(labeled_time) or (None, None)
    if hh is None:
        return None
    labeled = datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), int(hh), int(mm), tzinfo=JST)
    if semantics == SEMANTICS_BAR_START:
        start = labeled
        end = labeled + timedelta(minutes=1)
    else:
        end = labeled
        start = labeled - timedelta(minutes=1)
    available = end
    return {
        "bar_start_jst": start.isoformat(),
        "bar_end_jst": end.isoformat(),
        "available_at_jst": available.isoformat(),
        "bar_start_utc": start.astimezone(UTC).isoformat(),
        "bar_end_utc": end.astimezone(UTC).isoformat(),
        "available_at_utc": available.astimezone(UTC).isoformat(),
        "same_bar_close_entry": False,
        "feature_available_before_bar_end": False,
        "join_rule": "available_at_jst <= decision_time_jst",
        "timezone_policy": "zoneinfo_Asia/Tokyo_and_UTC_no_fixed_offset",
    }


def session_label_tse(hhmm: str) -> str:
    parsed = _hhmm(hhmm)
    if parsed is None:
        return "UNKNOWN"
    h, m = parsed
    minutes = h * 60 + m
    if minutes < 9 * 60:
        return "PREOPEN"
    if minutes < 11 * 60 + 30:
        return "TSE_AM"
    if minutes < 12 * 60 + 30:
        return "LUNCH"
    if minutes < 15 * 60 + 30:
        return "TSE_PM"
    return "POSTCLOSE"


assert infer_time_semantics(labeled_bar="09:00", tick_times=["09:00:00.067558", "09:00:01.039337"])[
    "EQUITY_MINUTE_TIME_SEMANTICS"
] == SEMANTICS_BAR_START
assert (
    infer_time_semantics(
        labeled_bar="09:00",
        tick_times=["08:59:00.100000", "09:00:00.200000"],
        bar_ohlcv={"O": 100.0, "H": 101.0, "L": 99.0, "C": 100.5, "Vo": 10.0},
        ticks_in_label_ohlcv={"O": 100.0, "H": 101.0, "L": 99.0, "C": 100.5, "Vo": 10.0},
        ticks_in_prev_ohlcv={"O": 98.0, "H": 98.0, "L": 98.0, "C": 98.0, "Vo": 1.0},
    )["EQUITY_MINUTE_TIME_SEMANTICS"]
    == SEMANTICS_BAR_START
)
assert infer_time_semantics(labeled_bar="09:00", tick_times=["08:59:00.100000", "08:59:59.900000"])[
    "EQUITY_MINUTE_TIME_SEMANTICS"
] == SEMANTICS_BAR_END
assert infer_time_semantics(labeled_bar="09:00", tick_times=[])["EQUITY_MINUTE_TIME_SEMANTICS"] == SEMANTICS_UNKNOWN
assert bar_clock(labeled_date="20260911", labeled_time="09:00", semantics=SEMANTICS_BAR_START)["available_at_jst"].endswith("09:01:00+09:00")
assert session_label_tse("09:00") == "TSE_AM"

"""Canonical timezone and causal timestamp gates. Naive datetime is forbidden."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from research.causal_driver_pb1 import CANONICAL_TZ
from research.causal_driver_pb1.contracts.errors import CausalTimeError

JST = ZoneInfo(CANONICAL_TZ)
UTC = ZoneInfo("UTC")


def canonical_tz() -> ZoneInfo:
    return JST


def assert_aware(ts: datetime, *, field: str) -> datetime:
    if not isinstance(ts, datetime):
        raise CausalTimeError(f"{field}_not_datetime")
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise CausalTimeError(f"{field}_naive_datetime_forbidden")
    return ts


def to_jst(ts: datetime, *, field: str) -> datetime:
    return assert_aware(ts, field=field).astimezone(JST)


def to_utc(ts: datetime, *, field: str) -> datetime:
    return assert_aware(ts, field=field).astimezone(UTC)


def iso(ts: datetime, *, field: str) -> str:
    return to_utc(ts, field=field).isoformat().replace("+00:00", "Z")


def bar_start_available_at(bar_start: datetime) -> datetime:
    """1m BAR_START is usable only after the bar closes (start + 1 minute)."""
    start = assert_aware(bar_start, field="event_time")
    return start + timedelta(minutes=1)


def causal_ok(*, available_at: datetime, decision_time: datetime) -> bool:
    a = assert_aware(available_at, field="available_at")
    d = assert_aware(decision_time, field="decision_time")
    return a <= d


def require_causal(*, available_at: datetime, decision_time: datetime) -> None:
    if not causal_ok(available_at=available_at, decision_time=decision_time):
        raise CausalTimeError("available_at_gt_decision_time")


def decide(*, available_at: datetime, decision_time: datetime) -> str:
    """ACCEPT or raise. Future values fail-closed. No silent skip."""
    require_causal(available_at=available_at, decision_time=decision_time)
    return "ACCEPT"


def validate_timestamp_order(
    *,
    event_time: datetime,
    available_at: datetime,
    received_at: datetime | None,
    bar_semantics: str | None,
) -> None:
    et = assert_aware(event_time, field="event_time")
    aa = assert_aware(available_at, field="available_at")
    if aa < et:
        raise CausalTimeError("available_at_before_event_time")
    if bar_semantics == "BAR_START" and aa < et + timedelta(minutes=1):
        raise CausalTimeError("bar_start_available_at_before_bar_end")
    if received_at is not None:
        rt = assert_aware(received_at, field="received_at")
        if rt < aa:
            raise CausalTimeError("received_at_before_available_at")


def jst(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=JST)


def as_jsonable(ts: datetime, *, field: str) -> str:
    return iso(ts, field=field)


def maybe_dt(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return assert_aware(value, field=field)
    raise CausalTimeError(f"{field}_not_datetime")

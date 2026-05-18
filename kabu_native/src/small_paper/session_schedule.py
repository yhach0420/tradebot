"""Trading session window helpers for full-day live dry-run (JST)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

MORNING_END = time(11, 0)
MIDDAY_END = time(12, 30)
AFTERNOON_END = time(15, 30)


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM: {value!r}")
    return time(int(parts[0]), int(parts[1]))


@dataclass(frozen=True)
class SessionSchedule:
    session_start: str
    session_end: str
    trade_date: date

    @property
    def start_dt(self) -> datetime:
        t = parse_hhmm(self.session_start)
        return datetime.combine(self.trade_date, t, tzinfo=JST)

    @property
    def end_dt(self) -> datetime:
        t = parse_hhmm(self.session_end)
        return datetime.combine(self.trade_date, t, tzinfo=JST)

    def is_in_session(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(JST)
        return self.start_dt <= now <= self.end_dt

    def is_before_session(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(JST)
        return now < self.start_dt

    def is_after_session(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(JST)
        return now > self.end_dt

    def seconds_until_start(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(JST)
        return max(0.0, (self.start_dt - now).total_seconds())

    def seconds_until_end(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(JST)
        return max(0.0, (self.end_dt - now).total_seconds())


def session_bucket(now: Optional[datetime] = None) -> str:
    """morning | midday | afternoon | outside."""
    now = now or datetime.now(JST)
    t = now.time()
    start = parse_hhmm("09:00")
    if t < start or t > AFTERNOON_END:
        return "outside"
    if t < MORNING_END:
        return "morning"
    if t < MIDDAY_END:
        return "midday"
    return "afternoon"


def empty_bucket_summary() -> dict[str, dict[str, int]]:
    return {
        "morning": {"candidate": 0, "accepted": 0, "rejected": 0},
        "midday": {"candidate": 0, "accepted": 0, "rejected": 0},
        "afternoon": {"candidate": 0, "accepted": 0, "rejected": 0},
    }


def wait_until(start: datetime, *, poll_sec: float = 30.0) -> None:
    import time

    while True:
        now = datetime.now(JST)
        if now >= start:
            return
        sleep_for = min(poll_sec, max(1.0, (start - now).total_seconds()))
        time.sleep(sleep_for)

"""Minimal injectable clock for demo / simulation (Phase687W65).

Production default remains wall-clock ``datetime.now(tz)``.
Simulations set a VirtualClock and pass ``now=`` / ``clock.now()`` explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class VirtualClock:
    """Mutable demo clock. Does not globally patch ``datetime.now`` by default."""

    _now: datetime
    history: list[datetime] = field(default_factory=list)

    @classmethod
    def at(cls, year: int, month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> "VirtualClock":
        return cls(_now=datetime(year, month, day, hour, minute, second, tzinfo=JST))

    @classmethod
    def from_iso(cls, iso: str) -> "VirtualClock":
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return cls(_now=dt.astimezone(JST))

    def now(self, tz: Optional[ZoneInfo] = None) -> datetime:
        n = self._now
        if tz is not None:
            return n.astimezone(tz)
        return n

    def set(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        self._now = dt.astimezone(JST)
        self.history.append(self._now)
        return self._now

    def set_hms(self, hour: int, minute: int = 0, second: int = 0) -> datetime:
        n = self._now
        return self.set(n.replace(hour=hour, minute=minute, second=second, microsecond=0))

    def advance(self, *, seconds: float = 0, minutes: float = 0) -> datetime:
        return self.set(self._now + timedelta(seconds=float(seconds) + float(minutes) * 60.0))

    def iso(self) -> str:
        return self._now.isoformat(timespec="seconds")

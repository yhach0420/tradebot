"""
Operational allowed trading windows (market-structure exclusion, not time-band optimization).

Fixed windows only — no session-specific thresholds or afternoon stop rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.session_schedule import parse_hhmm

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_ALLOWED_WINDOWS: tuple[tuple[str, str], ...] = (
    ("09:05", "11:23"),
    ("12:33", "15:20"),
)

REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW = "outside_allowed_trading_window"


@dataclass(frozen=True)
class TradingWindow:
    start: time
    end: time
    label: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TradingWindow":
        return cls(
            start=parse_hhmm(str(data.get("start", "09:05"))),
            end=parse_hhmm(str(data.get("end", "11:23"))),
            label=str(data.get("label", "")),
        )


def parse_allowed_trading_windows(
    raw: Optional[Sequence[Mapping[str, Any]]],
) -> list[TradingWindow]:
    if not raw:
        return [TradingWindow(parse_hhmm(s), parse_hhmm(e)) for s, e in DEFAULT_ALLOWED_WINDOWS]
    return [TradingWindow.from_mapping(w) for w in raw]


def entry_datetime(entry_time: str | datetime) -> Optional[datetime]:
    if isinstance(entry_time, datetime):
        dt = entry_time
    else:
        try:
            dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def is_in_allowed_trading_window(
    entry_time: str | datetime,
    windows: Sequence[TradingWindow],
) -> bool:
    dt = entry_datetime(entry_time)
    if dt is None:
        return False
    t = dt.time()
    for w in windows:
        if w.start <= t <= w.end:
            return True
    return False


def windows_summary(windows: Sequence[TradingWindow]) -> list[dict[str, str]]:
    return [
        {"start": w.start.strftime("%H:%M"), "end": w.end.strftime("%H:%M"), "label": w.label}
        for w in windows
    ]

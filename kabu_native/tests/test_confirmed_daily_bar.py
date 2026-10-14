"""Confirmed cash bar selection. No network and no orders."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from universe.opening_screen import confirmed_session, select_confirmed_bar, session_available_at

JST = ZoneInfo("Asia/Tokyo")
AS_OF = datetime(2026, 10, 13, 22, 49, tzinfo=JST)
SESSIONS = ["20261009", "20261013", "20261014"]


def _bar(day: str, close: float = 100.0) -> dict:
    return {"source_bar_date": day, "close": close, "high": close + 1, "low": close - 1, "volume": 1000.0}


def test_20261014_uses_the_20261013_confirmed_bar() -> None:
    required = confirmed_session(SESSIONS, "20261014", AS_OF)
    assert required == "20261013"
    assert session_available_at("20261013") <= AS_OF
    reason, bar = select_confirmed_bar(
        [_bar("20261009"), _bar("20261013", 1247), _bar("20261014", 1300)],
        required=required or "",
        target_ymd="20261014",
        as_of=AS_OF,
    )
    assert reason == ""
    assert bar is not None
    assert bar["source_bar_date"] == "20261013"
    assert bar["close"] == 1247


def test_unconfirmed_20261014_bar_is_not_the_required_session() -> None:
    before_close = datetime(2026, 10, 13, 15, 0, tzinfo=JST)
    assert confirmed_session(SESSIONS, "20261014", before_close) == "20261009"
    after_close = datetime(2026, 10, 14, 10, 0, tzinfo=JST)
    assert confirmed_session(SESSIONS, "20261014", after_close) == "20261013"


def test_older_bar_is_not_a_substitute() -> None:
    reason, bar = select_confirmed_bar(
        [_bar("20261009")],
        required="20261013",
        target_ymd="20261014",
        as_of=AS_OF,
    )
    assert reason == "STALE_BAR"
    assert bar is None


def test_missing_confirmed_session_fail_closed() -> None:
    assert confirmed_session(["20261014"], "20261014", AS_OF) is None

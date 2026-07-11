"""
Phase662 — Observer entry clock vs market board timestamp.

Observer holding duration uses accept-time (observer_entry_time), not stale
CurrentPriceTime. Market timestamps are preserved for audit and Discord.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from storage.intraday_recorder import parse_kabu_time

JST = ZoneInfo("Asia/Tokyo")

# Market timestamp lag vs accept time above this → stale_trade tag.
STALE_MARKET_LAG_SEC = 60.0

_ACCEPT_CLOCK_KEYS = (
    "accepted_at",
    "accepted_event_time",
    "eval_ts",
    "eval_end_ts",
    "event_time",
)

_MARKET_CLOCK_KEYS = (
    "market_entry_time",
    "current_price_time",
    "entry_time",
)


def _parse_optional_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    sentinel = datetime(1970, 1, 1, tzinfo=JST)
    dt = parse_kabu_time(value, fallback=sentinel)
    return None if dt == sentinel else dt


def resolve_observer_entry_time(
    trade: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    fallback_now: datetime | None = None,
) -> datetime:
    """Accept-time clock for observer hold duration (never stale board time alone)."""
    now = fallback_now or datetime.now(JST)
    for key in _ACCEPT_CLOCK_KEYS:
        dt = _parse_optional_time(trade.get(key))
        if dt is not None:
            return dt
    if payload:
        for key in _ACCEPT_CLOCK_KEYS:
            dt = _parse_optional_time(payload.get(key))
            if dt is not None:
                return dt
    return now


def resolve_market_entry_time(
    trade: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
) -> Optional[datetime]:
    """Board/market timestamp (CurrentPriceTime lineage)."""
    explicit = (
        _parse_optional_time(trade.get("market_entry_time"))
        or _parse_optional_time(trade.get("current_price_time"))
    )
    if explicit is not None:
        return explicit
    if payload:
        explicit = (
            _parse_optional_time(payload.get("market_entry_time"))
            or _parse_optional_time(payload.get("current_price_time"))
            or _parse_optional_time(payload.get("CurrentPriceTime"))
        )
        if explicit is not None:
            return explicit
    return _parse_optional_time(trade.get("entry_time"))


def market_time_age_sec(
    observer_entry_time: datetime,
    market_entry_time: Optional[datetime],
) -> Optional[float]:
    if market_entry_time is None:
        return None
    return max(0.0, (observer_entry_time - market_entry_time).total_seconds())


def is_stale_market_trade(
    observer_entry_time: datetime,
    market_entry_time: Optional[datetime],
    *,
    lag_threshold_sec: float = STALE_MARKET_LAG_SEC,
) -> bool:
    age = market_time_age_sec(observer_entry_time, market_entry_time)
    if age is None:
        return False
    return age > lag_threshold_sec


def observer_entry_fields(
    trade: Mapping[str, Any],
    *,
    payload: Mapping[str, Any] | None = None,
    fallback_now: datetime | None = None,
) -> dict[str, Any]:
    """Build timestamp fields for observer position + exit context."""
    observer_ent = resolve_observer_entry_time(trade, payload=payload, fallback_now=fallback_now)
    market_ent = resolve_market_entry_time(trade, payload=payload)
    accepted_raw = (
        trade.get("accepted_event_time")
        or trade.get("accepted_at")
        or (payload or {}).get("accepted_event_time")
        or (payload or {}).get("accepted_at")
    )
    accepted_evt = _parse_optional_time(accepted_raw) or observer_ent
    age = market_time_age_sec(observer_ent, market_ent)
    stale = is_stale_market_trade(observer_ent, market_ent)
    out: dict[str, Any] = {
        "observer_entry_time": observer_ent.isoformat(timespec="seconds"),
        "accepted_event_time": accepted_evt.isoformat(timespec="seconds"),
        "stale_trade": stale,
    }
    if market_ent is not None:
        out["market_entry_time"] = market_ent.isoformat(timespec="seconds")
        out["current_price_time"] = market_ent.isoformat(timespec="seconds")
    if age is not None:
        out["market_time_age_sec"] = round(age, 1)
    pas = trade.get("price_age_sec")
    if pas is not None:
        try:
            out["price_age_sec"] = round(float(pas), 1)
        except (TypeError, ValueError):
            pass
    return out

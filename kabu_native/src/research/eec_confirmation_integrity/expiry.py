"""EC2 candidate episode expiry (no indefinite reclaim retention)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from research.eec_confirmation_integrity.constants import (
    AM_FORCE_CLOSE_HM,
    DATA_GAP_SEC,
    HORIZON_SEC,
    LONG_GAP_SEC,
    PM_FORCE_CLOSE_HM,
)
from research.entry_exit_contract.contract import EntryContract
from research.price_flow_exit.path_mfe import PathBar


@dataclass
class ExpiryEvent:
    t: datetime
    reason: str
    i: int


def session_of(t: datetime) -> str:
    return "AM" if t.hour < 12 else "PM"


def session_close_at(t: datetime) -> datetime:
    h, m = AM_FORCE_CLOSE_HM if t.hour < 12 else PM_FORCE_CLOSE_HM
    return t.replace(hour=h, minute=m, second=0, microsecond=0)


def horizon_expiry_time(t0: datetime, horizon_sec: float = HORIZON_SEC) -> datetime:
    return t0 + timedelta(seconds=horizon_sec)


def find_episode_expiry(
    c: EntryContract,
    path: Sequence[PathBar],
    *,
    entry_i: int,
    horizon_sec: float = HORIZON_SEC,
) -> ExpiryEvent:
    """First expiry after candidate entry. Path may include pre-entry lookback."""
    pl = float(c.levels["pullback_low"])
    reclaim = float(c.levels["reclaim_level"])
    t0 = c.entry_time
    close_at = session_close_at(t0)
    horizon_at = horizon_expiry_time(t0, horizon_sec)
    sess0 = session_of(t0)
    last_t: Optional[datetime] = None
    quote_bad_streak = 0
    below_reclaim_since: Optional[datetime] = None
    seen_above = False

    for i in range(entry_i, len(path)):
        b = path[i]
        if b.t < t0:
            continue

        if last_t is not None:
            gap = (b.t - last_t).total_seconds()
            if gap >= LONG_GAP_SEC:
                return ExpiryEvent(last_t, "data_gap_or_refresh", i)
            if gap >= DATA_GAP_SEC:
                return ExpiryEvent(last_t, "data_gap", i)
        last_t = b.t

        if session_of(b.t) != sess0:
            return ExpiryEvent(b.t, "session_break", i)
        if b.t >= close_at:
            return ExpiryEvent(close_at, "session_close", i)
        if b.t >= horizon_at:
            return ExpiryEvent(horizon_at, "horizon_180", i)

        if b.bid is None or b.ask is None or b.px <= 0:
            quote_bad_streak += 1
            if quote_bad_streak >= 5:
                return ExpiryEvent(b.t, "quote_quality_loss", i)
        else:
            quote_bad_streak = 0

        if b.px < pl or (b.bid is not None and float(b.bid) < pl):
            return ExpiryEvent(b.t, "pullback_low_break", i)

        if b.px > reclaim:
            seen_above = True
            below_reclaim_since = None
        else:
            if below_reclaim_since is None:
                below_reclaim_since = b.t
            elif (b.t - below_reclaim_since).total_seconds() >= 8.0:
                return ExpiryEvent(below_reclaim_since, "reclaim_hypothesis_fail", i)

        # new independent pullback: after reclaim, print a new low under original pullback_low
        if seen_above and b.px < pl * 0.999:
            return ExpiryEvent(b.t, "new_independent_pullback", i)

    if path:
        b = path[-1]
        if b.t >= horizon_at:
            return ExpiryEvent(horizon_at, "horizon_180", len(path) - 1)
        return ExpiryEvent(b.t, "path_end", len(path) - 1)
    return ExpiryEvent(t0, "path_end", 0)

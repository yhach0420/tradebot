"""AM session clock. Decision minutes 09:10–11:25. Leader freshness uses bar_start T-1m or T-2m."""
from __future__ import annotations

from math import ceil

from research.causal_driver_pb1.cross_sectional_precommit import (
    AM_FIRST,
    AM_LAST,
    CLOCK_FIRST,
    CLOCK_LAST,
    HORIZON_LAST_T,
    LEADER_DAY_COVERAGE_MIN,
)


def hhmm_to_min(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


AM_START_MIN = hhmm_to_min(AM_FIRST)
AM_END_MIN = hhmm_to_min(AM_LAST)
N_AM = AM_END_MIN - AM_START_MIN + 1
CLOCK_MINS = tuple(range(hhmm_to_min(CLOCK_FIRST), hhmm_to_min(CLOCK_LAST) + 1))
N_CLOCK = len(CLOCK_MINS)
N_DECISION_MIN_OK = int(ceil(LEADER_DAY_COVERAGE_MIN * N_CLOCK))


def am_index(minute_from_midnight: int) -> int:
    return int(minute_from_midnight) - AM_START_MIN


def clock_ok_for_horizon(t_min: int, horizon: int) -> bool:
    last = hhmm_to_min(HORIZON_LAST_T[str(int(horizon))])
    return int(t_min) <= last


def leader_fresh_at_t(present_am, t_min: int) -> bool:
    """Age<=60s iff a completed bar exists with bar_start in {T-1m, T-2m}."""
    i1 = am_index(int(t_min) - 1)
    i2 = am_index(int(t_min) - 2)
    n = len(present_am)
    ok1 = 0 <= i1 < n and bool(present_am[i1])
    ok2 = 0 <= i2 < n and bool(present_am[i2])
    return ok1 or ok2

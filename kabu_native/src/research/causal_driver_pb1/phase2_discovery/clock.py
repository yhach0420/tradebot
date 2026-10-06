"""Native 1m research clock helpers. BAR_START: price at T uses bar_start T-1m."""
from __future__ import annotations

from research.causal_driver_pb1.phase2_precommit import CLOCK_FIRST, CLOCK_LAST, HORIZON_LAST_T
from research.causal_driver_pb1.phase2_discovery import GRID_FIRST, GRID_LAST


def hhmm_to_min(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def min_to_hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


GRID_START_MIN = hhmm_to_min(GRID_FIRST)
GRID_END_MIN = hhmm_to_min(GRID_LAST)
N_GRID = GRID_END_MIN - GRID_START_MIN + 1
CLOCK_MINS = tuple(range(hhmm_to_min(CLOCK_FIRST), hhmm_to_min(CLOCK_LAST) + 1))
N_CLOCK = len(CLOCK_MINS)


def grid_index(minute_from_midnight: int) -> int:
    return int(minute_from_midnight) - GRID_START_MIN


def bar_index_for_available_t(t_min: int) -> int:
    """Last completed bar with available_at <= T uses bar_start = T-1 minute."""
    return grid_index(int(t_min) - 1)


def clock_ok_for_horizon(t_min: int, horizon: int) -> bool:
    last = hhmm_to_min(HORIZON_LAST_T[str(int(horizon))])
    return int(t_min) <= last


def horizon_clock_indices(horizon: int) -> list[int]:
    return [i for i, t in enumerate(CLOCK_MINS) if clock_ok_for_horizon(t, horizon)]

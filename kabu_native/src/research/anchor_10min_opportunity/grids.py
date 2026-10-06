"""Derive Phase A midpoints and precommitted UNIFORM_10 from Runtime CLOCK_GRID.

Midpoints are computed from 20-minute gaps only. 15-minute 09:25→09:40 is not touched.
Start-minute search is forbidden: AM start = first canonical AM, PM start = first canonical PM.
"""
from __future__ import annotations

from typing import Any

from research.anchor_10min_opportunity import (
    AM_TAIL,
    PM_TAIL,
    UNIFORM_AM_LAST,
    UNIFORM_PM_LAST,
    UNIFORM_STEP_MIN,
)
from research.anchor_timing_robustness.grid import canonical_grid, hm_label, parse_hm, split_am_pm


def _minutes(h: int, m: int) -> int:
    return int(h) * 60 + int(m)


def _hm(total: int) -> tuple[int, int]:
    return (total // 60, total % 60)


def reference_tuples() -> tuple[tuple[int, int], ...]:
    return canonical_grid()


def reference_labels() -> list[str]:
    return [hm_label(h, m) for h, m in reference_tuples()]


def phase_a_added_tuples() -> tuple[tuple[int, int], ...]:
    """Exactly the midpoints of consecutive same-session 20-minute canonical gaps."""
    grid = list(reference_tuples())
    added: list[tuple[int, int]] = []
    for (h0, m0), (h1, m1) in zip(grid, grid[1:]):
        if (h0 < 12) != (h1 < 12):
            continue
        gap = _minutes(h1, m1) - _minutes(h0, m0)
        if gap == 20:
            mid = _minutes(h0, m0) + 10
            added.append(_hm(mid))
    return tuple(added)


def phase_a_added_labels() -> list[str]:
    return [hm_label(h, m) for h, m in phase_a_added_tuples()]


def augmented_tuples() -> tuple[tuple[int, int], ...]:
    return tuple(sorted(set(reference_tuples()) | set(phase_a_added_tuples()), key=lambda x: _minutes(*x)))


def _walk(start: tuple[int, int], last: tuple[int, int], step: int) -> tuple[tuple[int, int], ...]:
    t = _minutes(*start)
    end = _minutes(*last)
    out: list[tuple[int, int]] = []
    while t <= end:
        out.append(_hm(t))
        t += int(step)
    return tuple(out)


def uniform10_tuples() -> tuple[tuple[int, int], ...]:
    am, pm = split_am_pm(reference_tuples())
    am0 = parse_hm(am[0])
    pm0 = parse_hm(pm[0])
    return _walk(am0, UNIFORM_AM_LAST, UNIFORM_STEP_MIN) + _walk(pm0, UNIFORM_PM_LAST, UNIFORM_STEP_MIN)


def uniform10_labels() -> list[str]:
    return [hm_label(h, m) for h, m in uniform10_tuples()]


def uniform10_new_tuples() -> tuple[tuple[int, int], ...]:
    ref = set(reference_tuples())
    return tuple(x for x in uniform10_tuples() if x not in ref)


def tail_tuples() -> tuple[tuple[int, int], ...]:
    return tuple(AM_TAIL + PM_TAIL)


def tail_labels() -> list[str]:
    return [hm_label(h, m) for h, m in tail_tuples()]


def tod_family(anchor: str) -> str:
    lab = str(anchor)
    if lab in {"09:05", "09:15"}:
        return "OPEN_EARLY"
    if lab in tail_labels() or lab in {"11:05", "11:15", "15:00"}:
        return "SESSION_TAIL"
    return "NORMAL_SESSION"


def grid_contract() -> dict[str, Any]:
    ref = reference_labels()
    added = phase_a_added_labels()
    uni = uniform10_labels()
    return {
        "REFERENCE_ANCHORS": ref,
        "REFERENCE_N": len(ref),
        "PHASE_A_ADDED_ANCHORS": added,
        "PHASE_A_ADDED_N": len(added),
        "AUGMENTED_N": len(augmented_tuples()),
        "UNIFORM10_ANCHORS": uni,
        "UNIFORM10_N": len(uni),
        "UNIFORM10_NEW_ANCHORS": [hm_label(h, m) for h, m in uniform10_new_tuples()],
        "AM_TAIL": [hm_label(h, m) for h, m in AM_TAIL],
        "PM_TAIL": [hm_label(h, m) for h, m in PM_TAIL],
        "note": (
            "Midpoints are 20-minute-gap centers only. 09:25→09:40 (15 min) is unchanged. "
            "UNIFORM_10 uses canonical AM[0]/PM[0] and precommitted last slots; no start search."
        ),
    }

"""Canonical CLOCK_GRID from current Runtime. No hand-typed times. No clamp/remap."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.anchor_timing_robustness import (
    AM_END,
    AM_START,
    PM_END,
    PM_START,
    SHIFTS_MIN,
    SHIFT_KEYS,
    TOD_BUCKETS,
)
from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")


def canonical_grid() -> tuple[tuple[int, int], ...]:
    return tuple((int(h), int(m)) for h, m in CLOCK_GRID)


def hm_label(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def parse_hm(label: str) -> tuple[int, int]:
    h, m = str(label).split(":")
    return int(h), int(m)


def hm_epoch(day: str, h: int, m: int) -> float:
    return datetime(
        int(day[:4]), int(day[4:6]), int(day[6:]), int(h), int(m), 0, tzinfo=JST
    ).timestamp()


def session_of_epoch(day: str, t: float) -> Optional[str]:
    am0, am1 = hm_epoch(day, *AM_START), hm_epoch(day, *AM_END)
    pm0, pm1 = hm_epoch(day, *PM_START), hm_epoch(day, *PM_END)
    if am0 - 1e-12 <= float(t) <= am1 + 1e-12:
        return "AM"
    if pm0 - 1e-12 <= float(t) <= pm1 + 1e-12:
        return "PM"
    return None


def session_of_hm(day: str, h: int, m: int) -> Optional[str]:
    return session_of_epoch(day, hm_epoch(day, h, m))


def tod_bucket(anchor: str) -> str:
    for name, labels in TOD_BUCKETS.items():
        if str(anchor) in labels:
            return name
    return "OTHER"


def split_am_pm(grid: tuple[tuple[int, int], ...]) -> tuple[list[str], list[str]]:
    am = [hm_label(h, m) for h, m in grid if h < 12]
    pm = [hm_label(h, m) for h, m in grid if h >= 12]
    return am, pm


def shifted_epoch(day: str, h: int, m: int, shift_min: int) -> float:
    return hm_epoch(day, h, m) + float(shift_min) * 60.0


def shift_validity(*, day: str = "20260810") -> list[dict[str, Any]]:
    """Per canonical anchor × shift. Invalid shifts are not executed. No clamp."""
    rows: list[dict[str, Any]] = []
    for h, m in canonical_grid():
        label = hm_label(h, m)
        t_ref = hm_epoch(day, h, m)
        sess_ref = session_of_epoch(day, t_ref)
        for sh in SHIFTS_MIN:
            t1 = t_ref + float(sh) * 60.0
            sess1 = session_of_epoch(day, t1)
            dt1 = datetime.fromtimestamp(t1, JST)
            lunch = False
            if sess_ref == "AM" and sess1 == "PM":
                lunch = True
            if sess_ref == "PM" and sess1 == "AM":
                lunch = True
            valid = sess1 is not None and sess1 == sess_ref and not lunch
            reason = ""
            if not valid:
                reason = "INVALID_SESSION_BOUNDARY"
                if sess_ref and sess1 and sess1 != sess_ref:
                    reason = "INVALID_SESSION_BOUNDARY"
                elif sess1 is None and (dt1.hour * 60 + dt1.minute) < 9 * 60:
                    reason = "INVALID_SESSION_BOUNDARY"
                elif sess1 is None and (dt1.hour * 60 + dt1.minute) > 15 * 60:
                    reason = "INVALID_SESSION_BOUNDARY"
            rows.append(
                {
                    "anchor": label,
                    "tod_bucket": tod_bucket(label),
                    "shift_min": int(sh),
                    "shift_key": SHIFT_KEYS[sh],
                    "shifted_hhmm": hm_label(dt1.hour, dt1.minute),
                    "session_ref": sess_ref,
                    "session_shift": sess1,
                    "valid": bool(valid),
                    "reason": reason if not valid else "",
                }
            )
    return rows


def valid_shift_map(*, day: str = "20260810") -> dict[tuple[str, int], dict[str, Any]]:
    return {(r["anchor"], int(r["shift_min"])): r for r in shift_validity(day=day)}


def regular_clock_grid(step_min: int) -> list[tuple[int, int]]:
    """Secondary regular grid inside existing session bounds. Not a candidate replacement."""
    out: list[tuple[int, int]] = []
    am0 = AM_START[0] * 60 + AM_START[1]
    am1 = AM_END[0] * 60 + AM_END[1]
    pm0 = PM_START[0] * 60 + PM_START[1]
    pm1 = PM_END[0] * 60 + PM_END[1]
    t = am0
    while t < am1:  # do not place an ENTRY at AM session end
        out.append((t // 60, t % 60))
        t += int(step_min)
    t = pm0
    while t <= pm1:
        out.append((t // 60, t % 60))
        t += int(step_min)
    return out

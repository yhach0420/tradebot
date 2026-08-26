"""Existing CLOCK_GRID + existing session bounds. No new cutoff. No clamp/remap."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.fixed_anchor_mechanism_audit_p3_0 import CLOCK_OFFSETS_SEC
from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")

# Existing Dual Lane / P2-4A continuous-session bounds. Not a new policy.
AM_START, AM_END = (9, 0), (11, 30)
PM_START, PM_END = (12, 30), (15, 0)


def hm_label(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


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


def common_support_fixed_grid(*, day: str = "20260722") -> dict[str, Any]:
    """Drop original slots whose shifted time leaves Current ENTRY session for any offset.

    Does not clamp. Does not remap to another clock.
    """
    original = list(CLOCK_GRID)
    kept: list[tuple[int, int]] = []
    excluded: list[dict[str, Any]] = []
    for h, m in original:
        t0 = hm_epoch(day, h, m)
        reasons: list[str] = []
        if session_of_epoch(day, t0) is None:
            reasons.append("ORIGINAL_OUTSIDE_ENTRY_SESSION")
        for off in CLOCK_OFFSETS_SEC:
            ts = t0 + float(off)
            if session_of_epoch(day, ts) is None:
                sign = "+" if off > 0 else ""
                reasons.append(f"SHIFT_{sign}{off}_OUTSIDE_ENTRY_SESSION")
        if reasons:
            excluded.append(
                {
                    "anchor_time": hm_label(h, m),
                    "hour": h,
                    "minute": m,
                    "exclusion_reasons": reasons,
                }
            )
        else:
            kept.append((h, m))
    return {
        "original_grid": [hm_label(h, m) for h, m in original],
        "original_anchor_count": len(original),
        "common_support_grid": [hm_label(h, m) for h, m in kept],
        "common_support_anchor_count": len(kept),
        "excluded": excluded,
        "session_bounds": {
            "AM": {"start": hm_label(*AM_START), "end": hm_label(*AM_END)},
            "PM": {"start": hm_label(*PM_START), "end": hm_label(*PM_END)},
            "source": "existing Dual Lane SESSION_CLOSE + JPX continuous session; not a new cutoff",
        },
        "offsets_sec": list(CLOCK_OFFSETS_SEC),
        "clamp": False,
        "remap": False,
        "kept_hm": kept,
    }

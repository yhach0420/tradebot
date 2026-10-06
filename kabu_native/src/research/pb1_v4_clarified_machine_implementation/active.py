"""OPENING_DRIVE_ACTIVE. Seed is not permanent. No clock expiry."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation import (
    FAILED_ATTEMPT_N,
    OR_RECROSS_CLOSES,
    REPEATED_NO_EXPANSION_N,
    UNWIND_FRAC,
)
from research.pb1_v4_clarified_machine_implementation.seed import opposite_drive_established


def opening_net(*, sign: int, open_0900: Any, close_now: Any) -> float | None:
    if not (_finite(open_0900) and _finite(close_now)):
        return None
    return (float(close_now) - float(open_0900)) * float(sign)


def classify_active_loss(
    *,
    sign: int,
    close: float,
    or_high: float,
    or_low: float,
    open_0900: Any,
    peak_disp: Any,
    wick_only_n: int,
    micro_break_n: int,
    recross_closes: int,
    left: bool,
    five_m_no_expansion_n: int,
    both_or_extremes_revisited: bool,
) -> dict[str, Any]:
    flags: list[str] = []
    disp_now = opening_net(sign=sign, open_0900=open_0900, close_now=close)
    if _finite(peak_disp) and float(peak_disp) > 0 and disp_now is not None:
        given = float(peak_disp) - float(disp_now)
        if given >= float(UNWIND_FRAC) * float(peak_disp) and float(disp_now) < 0.25 * float(peak_disp):
            flags.append("DISPLACEMENT_UNWOUND")
    failed_n = int(wick_only_n) + int(micro_break_n)
    if failed_n >= int(FAILED_ATTEMPT_N) and int(five_m_no_expansion_n) >= 2:
        flags.append("REPEATED_FAILED_PROGRESS")
    if left and int(recross_closes) >= int(OR_RECROSS_CLOSES):
        flags.append("REPEATED_OR_RECROSS")
    if left and both_or_extremes_revisited:
        flags.append("TWO_SIDED_BALANCE_REESTABLISHED")
    if int(five_m_no_expansion_n) >= int(REPEATED_NO_EXPANSION_N):
        flags.append("STALE_RANGE_RESOLUTION")
    return {
        "lost": bool(flags),
        "flags": flags,
        "reason": flags[0] if flags else None,
        "clock_cutoff": False,
        "age_5m_gate": False,
        "disp_now": disp_now,
    }


def maybe_establish_opposite(*, bars: list[dict[str, Any]], seed_row: dict[str, Any], scale: Any) -> dict[str, Any]:
    fail = dict(seed_row.get("failed_open") or {})
    return opposite_drive_established(bars, seed=fail, scale=scale)

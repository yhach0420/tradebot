"""Opening thesis lost is a causal STATE. No 09:30 / 09:45 / 5-minute age gate."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, _hold_close
from research.pb1_v4_machine_implementation import FAILED_ATTEMPT_N, OR_RECROSS_CLOSES, UNWIND_FRAC


def opening_net(*, sign: int, open_0900: Any, close_now: Any) -> float | None:
    if not (_finite(open_0900) and _finite(close_now)):
        return None
    return (float(close_now) - float(open_0900)) * float(sign)


def classify_thesis(
    *,
    sign: int,
    close: float,
    high: float,
    low: float,
    or_high: float,
    or_low: float,
    open_0900: Any,
    peak_disp: Any,
    wick_only_n: int,
    micro_break_n: int,
    recross_closes: int,
    left: bool,
    broken: bool,
    five_m_no_expansion_n: int,
    both_or_extremes_revisited: bool,
) -> dict[str, Any]:
    or_mid = (float(or_high) + float(or_low)) / 2.0
    flags: list[str] = []
    disp_now = opening_net(sign=sign, open_0900=open_0900, close_now=close)
    if _finite(peak_disp) and float(peak_disp) > 0 and disp_now is not None:
        given = float(peak_disp) - float(disp_now)
        if given >= float(UNWIND_FRAC) * float(peak_disp) and float(disp_now) < 0.25 * float(peak_disp):
            flags.append("DISPLACEMENT_UNWOUND")
    failed_n = int(wick_only_n) + int(micro_break_n)
    if (not broken) and failed_n >= int(FAILED_ATTEMPT_N) and int(five_m_no_expansion_n) >= 2:
        flags.append("MULTIPLE_FAILED_BREAKS")
    if left and int(recross_closes) >= int(OR_RECROSS_CLOSES):
        flags.append("REPEATED_OR_RECROSS")
    if left and both_or_extremes_revisited:
        flags.append("TWO_SIDED_RANGE_REESTABLISHED")
    if left and int(five_m_no_expansion_n) >= 3:
        flags.append("LACK_OF_DIRECTIONAL_EXPANSION")
    lost = bool(flags)
    return {
        "lost": lost,
        "flags": flags,
        "reason": flags[0] if flags else None,
        "clock_cutoff": False,
        "age_5m_gate": False,
        "disp_now": disp_now,
        "or_mid": or_mid,
        "hold_or": _hold_close(float(close), float(or_high) if int(sign) > 0 else float(or_low), int(sign)),
    }

"""FIVE_M_CONTINUATION as a state. Not frozen 0.35×opening5m / 1.5×N1M."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.reaccel import bar_body, opposite_wick


def dir_close_loc(*, sign: int, high: float, low: float, close: float) -> float | None:
    loc = close_loc(high, low, close)
    if loc is None:
        return None
    return float(loc) if int(sign) > 0 else (1.0 - float(loc))


def classify_continuation(
    *,
    sign: int,
    bar: dict[str, Any],
    level: float,
    retest_high: Any,
    retest_low: Any,
) -> dict[str, Any]:
    o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
    if not (_finite(o) and _finite(h) and _finite(l) and _finite(c)):
        return {"ok": False, "reason": "incomplete_5m"}
    tests = float(l) <= float(level) <= float(h)
    if int(sign) > 0:
        defended = float(c) > float(level)
        beyond = _finite(retest_high) and float(c) > float(retest_high)
    else:
        defended = float(c) < float(level)
        beyond = _finite(retest_low) and float(c) < float(retest_low)
    body = bar_body(float(o), float(c))
    wick = opposite_wick(sign=sign, open_px=float(o), high=float(h), low=float(l), close=float(c))
    counter_weakened = _finite(body) and _finite(wick) and float(body) >= float(wick)
    loc = dir_close_loc(sign=sign, high=float(h), low=float(l), close=float(c))
    loc_ok = loc is not None and float(loc) >= 0.50
    ok = bool(defended and loc_ok and (counter_weakened or beyond))
    return {
        "ok": ok,
        "reason": "location_defended_counter_weakened_or_renewed_progress" if ok else "no_5m_continuation_state",
        "location_still_defended": defended,
        "counter_pressure_weakened": counter_weakened,
        "renewed_5m_progress": beyond,
        "tested_level": tests,
        "close_loc_dir": loc,
        "range_over_opening5_required": False,
        "range_over_n1m_required": False,
        "legacy_s4_035_15_required": False,
    }

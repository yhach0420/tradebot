"""S4 FIVE_M_CONTINUATION_STATE. Visible on 5m. Not a huge completed breakout."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.reaccel import opposite_wick
from research.pb1_v4_machine_implementation import S4_BODY_FRAC, S4_CLOSE_LOC, S4_RANGE_OVER_N1M, S4_RANGE_OVER_OPEN5


def dir_close_loc(*, sign: int, high: float, low: float, close: float) -> float | None:
    loc = close_loc(high, low, close)
    if not _finite(loc):
        return None
    return float(loc) if int(sign) > 0 else float(1.0 - loc)


def classify_s4(
    *,
    sign: int,
    bar: dict[str, Any],
    retest_high: Any,
    retest_low: Any,
    normal_opening_5m: Any,
    n1m: Any,
) -> dict[str, Any]:
    o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
    if not (_finite(o) and _finite(h) and _finite(l) and _finite(c)):
        return {"ok": False, "reason": "incomplete_5m_bar"}
    body = abs(float(c) - float(o))
    rng = float(h) - float(l)
    dloc = dir_close_loc(sign=sign, high=float(h), low=float(l), close=float(c))
    wick = opposite_wick(sign=sign, open_px=float(o), high=float(h), low=float(l), close=float(c))
    dir_body = (float(c) - float(o)) * float(sign) > 0
    beyond = False
    if int(sign) > 0:
        beyond = _finite(retest_high) and float(c) > float(retest_high)
    else:
        beyond = _finite(retest_low) and float(c) < float(retest_low)
    range_ok = False
    if _finite(normal_opening_5m) and float(normal_opening_5m) > 0 and rng >= float(S4_RANGE_OVER_OPEN5) * float(normal_opening_5m):
        range_ok = True
    if _finite(n1m) and float(n1m) > 0 and rng >= float(S4_RANGE_OVER_N1M) * float(n1m):
        range_ok = True
    body_ok = rng > 0 and (body / rng) >= float(S4_BODY_FRAC)
    loc_ok = dloc is not None and float(dloc) >= float(S4_CLOSE_LOC)
    failed_counter = _finite(wick) and float(body) >= float(wick)
    ok = bool(dir_body and loc_ok and body_ok and range_ok and (beyond or failed_counter))
    reason = "five_m_continuation_not_visible"
    if not dir_body or not loc_ok:
        reason = "no_directional_5m_close_progression"
    elif not range_ok or not body_ok:
        reason = "no_renewed_5m_body_expansion"
    elif not (beyond or failed_counter):
        reason = "counter_pressure_not_weakened"
    if ok:
        reason = "five_m_continuation_restarting"
    return {
        "ok": ok,
        "reason": reason,
        "close_loc_dir": dloc,
        "body_over_range": (body / rng) if rng > 0 else None,
        "range": rng,
        "beyond_retest_extreme": beyond,
        "failed_counter_push": failed_counter,
        "huge_breakout_required": False,
    }

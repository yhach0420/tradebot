"""E0 5m confirmed execution. E1 1m timed execution. Same THESIS_READY. No identity mutation."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.reaccel import bar_body, is_micro_cross, opposite_wick
from research.pb1_v4_clarified_machine_correction_v2 import E1_BODY_N1M, E1_NET_N1M, E1_RANGE_N1M, EXEC_1M_CONFIRMED
from research.pb1_v4_clarified_machine_correction_v2.continuation import dir_close_loc


def three_bar_dir_net(*, rec: dict[str, Any], pos: int, sign: int) -> float | None:
    if pos < 2:
        return None
    c0, c1 = rec["c"][pos], rec["c"][pos - 2]
    if not (_finite(c0) and _finite(c1)):
        return None
    return (float(c0) - float(c1)) * float(sign)


def classify_e1(
    *,
    sign: int,
    rec: dict[str, Any],
    pos: int,
    open_px: float,
    high: float,
    low: float,
    close: float,
    retest_high: Any,
    retest_low: Any,
    n1m: Any,
    thesis_ready: bool,
    thesis_lost: bool,
) -> dict[str, Any]:
    if not thesis_ready:
        return {"ok": False, "state": "BLOCKED", "reason": "THESIS_NOT_READY", "revived_rejected_setup": False, "tv_gated": False}
    if thesis_lost:
        return {
            "ok": False,
            "state": "E1_ATTEMPT_CANCELLED",
            "reason": "THESIS_LOST",
            "revived_rejected_setup": False,
            "tv_gated": False,
        }
    if not _finite(n1m) or float(n1m) <= 0:
        return {"ok": False, "state": "WAIT", "reason": "no_prior_NORMAL_1M_RANGE", "tv_gated": False}
    net = three_bar_dir_net(rec=rec, pos=pos, sign=sign)
    rng = float(high) - float(low)
    body = bar_body(open_px, close)
    wick = opposite_wick(sign=sign, open_px=open_px, high=high, low=low, close=close)
    crossed = is_micro_cross(sign=sign, close=close, retest_high=retest_high, retest_low=retest_low)
    net_n = (float(net) / float(n1m)) if net is not None else None
    rng_n = rng / float(n1m)
    body_n = body / float(n1m)
    net_ok = net_n is not None and float(net_n) >= float(E1_NET_N1M)
    rng_ok = rng_n >= float(E1_RANGE_N1M)
    body_ok = body_n >= float(E1_BODY_N1M)
    wick_ok = _finite(body) and _finite(wick) and float(body) >= float(wick)
    dloc = dir_close_loc(sign=sign, high=high, low=low, close=close)
    if net_ok and rng_ok and body_ok and wick_ok and crossed:
        return {
            "ok": True,
            "state": EXEC_1M_CONFIRMED,
            "reason": "1m_state_change_at_preidentified_level",
            "three_bar_dir_net_over_NORMAL_1M_RANGE": net_n,
            "trigger_range_over_NORMAL_1M_RANGE": rng_n,
            "trigger_body_over_NORMAL_1M_RANGE": body_n,
            "micro_cross": True,
            "micro_cross_sufficient_alone": False,
            "tv_gated": False,
            "revived_rejected_setup": False,
            "alters_location_id": False,
            "alters_direction": False,
            "alters_opening_drive_id": False,
            "close_loc_dir": dloc,
            "thresholds_optimized": False,
        }
    if crossed and not (net_ok and rng_ok and body_ok):
        return {
            "ok": False,
            "state": "WAIT",
            "reason": "micro_cross_not_sufficient_alone",
            "micro_cross": True,
            "micro_cross_sufficient_alone": False,
            "tv_gated": False,
        }
    return {
        "ok": False,
        "state": "WAIT",
        "reason": "1m_state_change_not_visible",
        "micro_cross": crossed,
        "tv_gated": False,
    }

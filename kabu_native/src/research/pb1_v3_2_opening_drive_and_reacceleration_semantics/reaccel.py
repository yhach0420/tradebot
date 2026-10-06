"""REACCELERATION_TRIGGER_V2. Defended retest → expansion → close through micro structure."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite, close_loc
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics import REACCEL_CLOSE_LOC


def opposite_wick(*, sign: int, open_px: float, high: float, low: float, close: float) -> float:
    if int(sign) > 0:
        return float(min(open_px, close) - low)
    return float(high - max(open_px, close))


def bar_body(open_px: float, close: float) -> float:
    return abs(float(close) - float(open_px))


def dir_close_loc(*, sign: int, high: float, low: float, close: float) -> float | None:
    loc = close_loc(high, low, close)
    if not _finite(loc):
        return None
    return float(loc) if int(sign) > 0 else float(1.0 - loc)


def is_micro_cross(*, sign: int, close: float, retest_high: Any, retest_low: Any) -> bool:
    if int(sign) > 0:
        return _finite(retest_high) and float(close) > float(retest_high)
    return _finite(retest_low) and float(close) < float(retest_low)


def reclaim_distance(*, sign: int, close: float, retest_high: Any, retest_low: Any) -> float:
    if int(sign) > 0:
        if not _finite(retest_high):
            return float("nan")
        return float(close) - float(retest_high)
    if not _finite(retest_low):
        return float("nan")
    return float(retest_low) - float(close)


def classify_reacceleration(
    *,
    sign: int,
    open_px: float,
    high: float,
    low: float,
    close: float,
    prev_close: Any,
    retest_high: Any,
    retest_low: Any,
    retest_range: Any,
    n1m: Any,
) -> dict[str, Any]:
    """ONE ordinal family. Not reclaim_move>=1.0. Not from profit."""
    crossed = is_micro_cross(sign=sign, close=close, retest_high=retest_high, retest_low=retest_low)
    trig_range = float(high) - float(low) if _finite(high) and _finite(low) else float("nan")
    body = bar_body(open_px, close) if _finite(open_px) else float("nan")
    wick = opposite_wick(sign=sign, open_px=float(open_px) if _finite(open_px) else float(close), high=high, low=low, close=close)
    dloc = dir_close_loc(sign=sign, high=high, low=low, close=close)
    rec = reclaim_distance(sign=sign, close=close, retest_high=retest_high, retest_low=retest_low)
    two_net = (float(close) - float(prev_close)) * int(sign) if _finite(prev_close) else float("nan")
    expand_vs_retest = None
    if _finite(trig_range) and _finite(retest_range) and float(retest_range) > 0:
        expand_vs_retest = float(trig_range) / float(retest_range)
    reason = None
    ok = False
    if not crossed:
        reason = "no_micro_cross"
    elif dloc is None or float(dloc) < float(REACCEL_CLOSE_LOC):
        reason = "close_not_directional_half"
    elif not _finite(body) or not _finite(wick) or float(body) < float(wick):
        reason = "wick_dominated"
    elif _finite(retest_range) and float(retest_range) > 0 and _finite(trig_range) and float(trig_range) < float(retest_range):
        reason = "no_expansion_vs_retest"
    else:
        ok = True
        reason = "reacceleration"
    def _over(x: Any) -> float | None:
        if not (_finite(x) and _finite(n1m) and float(n1m) > 0):
            return None
        return float(x) / float(n1m)
    return {
        "ok": ok,
        "reason": reason,
        "trigger_range_over_NORMAL_1M_RANGE": _over(trig_range),
        "trigger_body_over_NORMAL_1M_RANGE": _over(body),
        "trigger_dir_close_loc": dloc,
        "reclaim_move_over_NORMAL_1M_RANGE": _over(rec),
        "two_bar_net_over_NORMAL_1M_RANGE": _over(two_net),
        "retest_extreme_to_trigger_close_over_NORMAL_1M_RANGE": _over(rec),
        "trigger_range": float(trig_range) if _finite(trig_range) else None,
        "trigger_body": float(body) if _finite(body) else None,
        "opposite_wick": float(wick) if _finite(wick) else None,
        "expand_vs_retest": expand_vs_retest,
        "reclaim_move": float(rec) if _finite(rec) else None,
        "reclaim_1n1m_gated": False,
        "reclaim_threshold_from_profit": False,
    }

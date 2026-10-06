"""Frozen precursor predicates. Sign and ordering only. No threshold fit."""
from __future__ import annotations

from typing import Any, Optional


def rate(ret: Optional[float], sec: int) -> Optional[float]:
    if ret is None:
        return None
    return float(ret) / float(sec)


def decelerating(ret30: Optional[float], ret60: Optional[float], ret180: Optional[float]) -> Optional[bool]:
    r30 = rate(ret30, 30)
    r60 = rate(ret60, 60)
    r180 = rate(ret180, 180)
    if r30 is None or r60 is None or r180 is None:
        return None
    return bool(r30 > r60 and r60 > r180)


def short_rebound(ret30: Optional[float]) -> Optional[bool]:
    if ret30 is None:
        return None
    return bool(ret30 > 0)


def short_rebound_class(
    nk30: Optional[float], tx30: Optional[float]
) -> Optional[str]:
    a = short_rebound(nk30)
    b = short_rebound(tx30)
    if a is None or b is None:
        return None
    if a and b:
        return "A_BOTH_30S_UP"
    if a or b:
        return "B_ONE_30S_UP"
    return "C_BOTH_30S_STILL_DOWN"


def both_short_rebound(nk30: Optional[float], tx30: Optional[float]) -> Optional[bool]:
    cls = short_rebound_class(nk30, tx30)
    if cls is None:
        return None
    return cls == "A_BOTH_30S_UP"


def both_decelerating(
    nk30: Optional[float],
    nk60: Optional[float],
    nk180: Optional[float],
    tx30: Optional[float],
    tx60: Optional[float],
    tx180: Optional[float],
) -> Optional[bool]:
    a = decelerating(nk30, nk60, nk180)
    b = decelerating(tx30, tx60, tx180)
    if a is None or b is None:
        return None
    return bool(a and b)


def cash_relative_resilience(cash_ew_180: Optional[float], fut_comp_180: Optional[float]) -> Optional[bool]:
    if cash_ew_180 is None or fut_comp_180 is None:
        return None
    return bool(float(cash_ew_180) > float(fut_comp_180))


def activity_relative_state(top_pre: Optional[float], bottom_pre: Optional[float]) -> Optional[float]:
    if top_pre is None or bottom_pre is None:
        return None
    return float(top_pre) - float(bottom_pre)


def p4_activity_sold_harder(state: Optional[float]) -> Optional[bool]:
    if state is None:
        return None
    return bool(state < 0)


def combo_and(a: Optional[bool], b: Optional[bool]) -> Optional[bool]:
    if a is None or b is None:
        return None
    return bool(a and b)


def precursors(
    *,
    nk30: Optional[float],
    nk60: Optional[float],
    nk180: Optional[float],
    tx30: Optional[float],
    tx60: Optional[float],
    tx180: Optional[float],
    cash_ew_180: Optional[float],
    top_pre: Optional[float],
    bottom_pre: Optional[float],
) -> dict[str, Any]:
    fut_comp = None
    if nk180 is not None and tx180 is not None:
        fut_comp = 0.5 * (float(nk180) + float(tx180))
    p1 = both_short_rebound(nk30, tx30)
    p2 = both_decelerating(nk30, nk60, nk180, tx30, tx60, tx180)
    p3 = cash_relative_resilience(cash_ew_180, fut_comp)
    rel = activity_relative_state(top_pre, bottom_pre)
    p4 = p4_activity_sold_harder(rel)
    return {
        "short_rebound_class": short_rebound_class(nk30, tx30),
        "futures_composite_ret_180": fut_comp,
        "activity_relative_state": rel,
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "P4": p4,
        "C1": combo_and(p1, p4),
        "C2": combo_and(p2, p4),
    }

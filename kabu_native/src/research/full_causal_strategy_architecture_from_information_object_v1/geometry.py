"""FULL_DEPTH_GEOMETRY price ranks. Qty only for snapshot validity / semantic audit. No imbalance alpha."""
from __future__ import annotations

from typing import Any, Optional

LEVELS = tuple(range(1, 11))


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def level_px_qty(pay: dict[str, Any], prefix: str) -> tuple[list[Optional[float]], list[Optional[float]]]:
    px: list[Optional[float]] = []
    qty: list[Optional[float]] = []
    for k in LEVELS:
        lv = pay.get(f"{prefix}{k}")
        if not isinstance(lv, dict):
            px.append(None)
            qty.append(None)
            continue
        px.append(_f(lv.get("Price")))
        qty.append(_f(lv.get("Qty")))
    return px, qty


def internal_zero_then_nonzero(qty: list[Optional[float]]) -> bool:
    for k in range(9):
        a = qty[k]
        b = qty[k + 1]
        if a is not None and b is not None and float(a) <= 0.0 and float(b) > 0.0:
            return True
    return False


def all10_present(px: list[Optional[float]], qty: list[Optional[float]]) -> bool:
    if len(px) != 10 or len(qty) != 10:
        return False
    for p, q in zip(px, qty):
        if p is None or q is None:
            return False
        if float(p) <= 0.0 or float(q) <= 0.0:
            return False
    return True


def strict_bid_order(px: list[Optional[float]]) -> bool:
    for i in range(9):
        a = px[i]
        b = px[i + 1]
        if a is None or b is None:
            return False
        if not (float(a) > float(b)):
            return False
    return True


def strict_ask_order(px: list[Optional[float]]) -> bool:
    for i in range(9):
        a = px[i]
        b = px[i + 1]
        if a is None or b is None:
            return False
        if not (float(a) < float(b)):
            return False
    return True


def valid_full_depth(
    bid_px: list[Optional[float]],
    bid_qty: list[Optional[float]],
    ask_px: list[Optional[float]],
    ask_qty: list[Optional[float]],
) -> bool:
    if not all10_present(bid_px, bid_qty):
        return False
    if not all10_present(ask_px, ask_qty):
        return False
    if not strict_bid_order(bid_px):
        return False
    if not strict_ask_order(ask_px):
        return False
    b1 = bid_px[0]
    s1 = ask_px[0]
    if b1 is None or s1 is None:
        return False
    return float(b1) < float(s1)


def bid_span(bid_px: list[Optional[float]]) -> Optional[float]:
    a = bid_px[0]
    b = bid_px[9]
    if a is None or b is None:
        return None
    sp = float(a) - float(b)
    if sp == sp and sp > 0.0:
        return sp
    return None


def ask_span(ask_px: list[Optional[float]]) -> Optional[float]:
    a = ask_px[9]
    b = ask_px[0]
    if a is None or b is None:
        return None
    sp = float(a) - float(b)
    if sp == sp and sp > 0.0:
        return sp
    return None


def long_geometry_state(ask_sp: float, bid_sp: float) -> bool:
    return float(ask_sp) > float(bid_sp)


def snapshot_from_payload(pay: dict[str, Any]) -> dict[str, Any]:
    bid_px, bid_qty = level_px_qty(pay, "Buy")
    ask_px, ask_qty = level_px_qty(pay, "Sell")
    valid = valid_full_depth(bid_px, bid_qty, ask_px, ask_qty)
    bspan = bid_span(bid_px) if valid else None
    aspan = ask_span(ask_px) if valid else None
    known = valid and bspan is not None and aspan is not None
    long_state: Optional[bool]
    if not known:
        long_state = None
    else:
        long_state = long_geometry_state(float(aspan), float(bspan))
    return {
        "VALID_FULL_DEPTH": bool(valid and known),
        "UNKNOWN": not bool(valid and known),
        "LONG_GEOMETRY_STATE": long_state,
        "BID_SPAN": bspan,
        "ASK_SPAN": aspan,
        "bid_all10": all10_present(bid_px, bid_qty),
        "ask_all10": all10_present(ask_px, ask_qty),
        "bid_strict": strict_bid_order(bid_px),
        "ask_strict": strict_ask_order(ask_px),
        "bid_internal_zero_then_nonzero": internal_zero_then_nonzero(bid_qty),
        "ask_internal_zero_then_nonzero": internal_zero_then_nonzero(ask_qty),
    }


def onset_false_to_true(prev_known: Optional[bool], cur_known: Optional[bool]) -> bool:
    if prev_known is None or cur_known is None:
        return False
    return (prev_known is False) and (cur_known is True)


def update_last_known(prev_known: Optional[bool], cur_known: Optional[bool]) -> Optional[bool]:
    if cur_known is None:
        return prev_known
    return bool(cur_known)


def semantic_valid(*, snapshot_n: int, bid_hole_n: int, ask_hole_n: int, rate_max: float) -> bool:
    if int(snapshot_n) <= 0:
        return False
    br = float(bid_hole_n) / float(snapshot_n)
    ar = float(ask_hole_n) / float(snapshot_n)
    return br <= float(rate_max) and ar <= float(rate_max)

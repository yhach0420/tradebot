"""Conservative 1-minute buy-limit simulation. Order must exist before the fill bar."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.multi_touch_daily_zone_1m_price_action_v1 import SESSION_FLAT


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def try_fill(*, limit_price: float, o: float, h: float, l: float) -> dict[str, Any] | None:
    if not _finite(limit_price) or not _finite(o) or not _finite(l):
        return None
    if l > limit_price:
        return None
    if o < limit_price:
        return {"filled": True, "fill_price": float(limit_price), "gapped_through": True, "price_improvement_claimed": False}
    if l <= limit_price <= o:
        return {"filled": True, "fill_price": float(limit_price), "gapped_through": False, "price_improvement_claimed": False}
    if l <= limit_price:
        return {"filled": True, "fill_price": float(limit_price), "gapped_through": False, "price_improvement_claimed": False}
    return None


def new_limit_order(*, placed_at: str, limit_price: float, zone: dict[str, Any], break_event: dict[str, Any]) -> dict[str, Any] | None:
    eligible = hhmm_add(placed_at, 1)
    if not eligible:
        return None
    return {
        "order_placed_at": placed_at,
        "limit_price": float(limit_price),
        "first_fill_eligible_bar": eligible,
        "fill_time": None,
        "fill_price": None,
        "cancel_time": None,
        "cancel_reason": None,
        "filled": False,
        "zone_id": zone.get("zone_id"),
        "touch_bucket": zone.get("touch_bucket"),
        "break_feature_bar": break_event.get("feature_bar"),
        "same_bar_placement_and_fill": False,
        "symbol": break_event.get("symbol"),
        "date": break_event.get("date"),
        "block": break_event.get("block"),
        "sector": break_event.get("sector"),
        "zone_lo": zone.get("lo"),
        "zone_hi": zone.get("hi"),
        "level_value": zone.get("lo"),
    }


def step_pending(order: dict[str, Any], *, t: str, o: float, h: float, l: float, c: float) -> None:
    if order.get("filled") or order.get("cancel_reason"):
        return
    if in_lunch(t):
        return
    if t < str(order["first_fill_eligible_bar"]):
        return
    if t >= SESSION_FLAT:
        order["cancel_time"] = t
        order["cancel_reason"] = "session_flat_unfilled"
        return
    got = try_fill(limit_price=float(order["limit_price"]), o=o, h=h, l=l)
    if got:
        order["filled"] = True
        order["fill_time"] = t
        order["fill_price"] = got["fill_price"]
        order["gapped_through"] = got["gapped_through"]
        return
    if _finite(c) and _finite(order.get("zone_lo")) and float(c) < float(order["zone_lo"]):
        order["cancel_time"] = t
        order["cancel_reason"] = "fail_back_below_before_fill"
        order["failed_before_fill"] = True

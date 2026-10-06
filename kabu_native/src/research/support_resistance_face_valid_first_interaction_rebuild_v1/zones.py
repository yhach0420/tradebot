"""Cluster independently confirmed swing reactions. Active only after the 2nd is knowable. Broken by prior close-through."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import LOOKBACK_DAYS, MIN_TOUCHES, ZONE_HALF_ATR


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _median(xs: list[float]) -> float:
    return float(np.median(np.asarray(xs, dtype=float)))


def _broken_date(z: dict[str, Any], hist: list[dict[str, Any]], session_date: str) -> str | None:
    role = str(z.get("role") or "")
    hi, lo = float(z["hi"]), float(z["lo"])
    act = str(z.get("ZONE_ACTIVATED_AT") or "")
    for d in hist:
        dt = str(d.get("date") or "")
        if dt >= session_date:
            continue
        if act and dt < act:
            continue
        c = float(d.get("close") or 0)
        if not _finite(c):
            continue
        if role == "RESISTANCE" and c > hi:
            return dt
        if role == "SUPPORT" and c < lo:
            return dt
    return None


def cluster_role(
    reactions: list[dict[str, Any]],
    *,
    atr: float,
    session_date: str,
    lookback_dates: set[str],
    hist: list[dict[str, Any]],
    role: str,
) -> list[dict[str, Any]]:
    if not _finite(atr) or atr <= 0:
        return []
    hw = float(ZONE_HALF_ATR) * float(atr)
    width = 2.0 * hw
    xs = [
        r
        for r in reactions
        if str(r.get("role")) == role
        and str(r.get("available_from") or "") <= session_date
        and str(r.get("pivot_date") or r.get("reaction_date") or "") in lookback_dates
        and _finite(r.get("pivot_price") or r.get("price"))
        and str(r.get("confirmation_date") or "") < session_date
    ]
    if not xs:
        return []
    xs = sorted(xs, key=lambda r: (float(r.get("pivot_price") or r["price"]), str(r.get("pivot_date") or "")))
    groups: list[list[dict[str, Any]]] = []
    cur = [xs[0]]
    for r in xs[1:]:
        if float(r.get("pivot_price") or r["price"]) - float(cur[0].get("pivot_price") or cur[0]["price"]) <= width:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    zones = []
    for g in groups:
        prices = [float(x.get("pivot_price") or x["price"]) for x in g]
        cycles = sorted({str(x.get("pivot_date") or "") for x in g})
        n_cycles = len(cycles)
        center = _median(prices)
        second = cycles[1] if n_cycles >= MIN_TOUCHES else None
        activated = None
        hit2 = None
        if second:
            hit2 = next(x for x in g if str(x.get("pivot_date") or "") == second)
            activated = str(hit2.get("available_from") or "")
        first = min(g, key=lambda x: str(x.get("pivot_date") or ""))
        last = max(g, key=lambda x: str(x.get("pivot_date") or ""))
        z = {
            "zone_id": f"{g[0].get('symbol') or ''}:{role}:{first.get('pivot_date')}:{round(center, 2)}",
            "role": role,
            "center": center,
            "lo": center - hw,
            "hi": center + hw,
            "half_width": hw,
            "atr": atr,
            "touch_count": len(g),
            "distinct_swing_cycles": n_cycles,
            "first_pivot_date": first.get("pivot_date"),
            "last_pivot_date": last.get("pivot_date"),
            "last_confirmation_date": last.get("confirmation_date"),
            "ZONE_ACTIVATED_AT": activated,
            "members": g,
            "compactness_atr": ((max(prices) - min(prices)) / atr) if len(prices) >= 2 and atr > 0 else 0.0,
            "reaction_magnitude_atr": float(np.mean([float(x.get("move_away_atr") or 0) for x in g])),
            "symbol_specific": True,
            "future_touches_used": False,
            "future_pivot": False,
        }
        broken = _broken_date(z, hist, session_date)
        z["broken_date"] = broken
        z["state"] = (
            "BROKEN_RESISTANCE"
            if (broken and role == "RESISTANCE")
            else ("BROKEN_SUPPORT" if broken and role == "SUPPORT" else ("ACTIVE_RESISTANCE" if role == "RESISTANCE" else "ACTIVE_SUPPORT"))
        )
        if broken:
            z["state"] = "BROKEN_RESISTANCE_CANDIDATE" if role == "RESISTANCE" else "BROKEN_SUPPORT_CANDIDATE"
        knowable = bool(activated and activated <= session_date and n_cycles >= MIN_TOUCHES)
        z["active"] = bool(knowable and broken is None)
        z["broken_candidate"] = bool(knowable and broken is not None)
        conf2 = str((hit2 or {}).get("confirmation_date") or "")
        z["retroactive"] = bool(activated and conf2 and activated <= conf2)
        zones.append(z)
    return zones


def snapshot_zones(
    *,
    symbol: str,
    reactions: list[dict[str, Any]],
    atr: float,
    session_date: str,
    lookback_dates: list[str],
    hist: list[dict[str, Any]],
) -> dict[str, Any]:
    lb = set(lookback_dates[-LOOKBACK_DAYS:]) if lookback_dates else set()
    tagged = [{**r, "symbol": symbol} for r in reactions]
    res = cluster_role(tagged, atr=atr, session_date=session_date, lookback_dates=lb, hist=hist, role="RESISTANCE")
    sup = cluster_role(tagged, atr=atr, session_date=session_date, lookback_dates=lb, hist=hist, role="SUPPORT")
    return {
        "atr": atr,
        "resistance_active": [z for z in res if z["active"]],
        "support_active": [z for z in sup if z["active"]],
        "resistance_broken": [z for z in res if z["broken_candidate"]],
        "support_broken": [z for z in sup if z["broken_candidate"]],
        "resistance_all": res,
        "support_all": sup,
        "retroactive_zone_n": sum(1 for z in res + sup if z.get("retroactive")),
        "already_broken_active_n": sum(1 for z in res + sup if z.get("active") and z.get("broken_date")),
        "future_pivot_n": sum(1 for r in tagged if r.get("future_pivot")),
    }

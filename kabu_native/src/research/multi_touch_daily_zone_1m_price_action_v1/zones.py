"""Cluster confirmed reactions into symbol-specific zones. Active only after 2nd touch is knowable."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.multi_touch_daily_zone_1m_price_action_v1 import LOOKBACK_DAYS, MIN_TOUCHES, ZONE_HALF_ATR


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _median(xs: list[float]) -> float:
    arr = np.asarray(xs, dtype=float)
    return float(np.median(arr))


def cluster_role(
    reactions: list[dict[str, Any]],
    *,
    atr: float,
    session_date: str,
    lookback_dates: set[str],
    half_mult: float = ZONE_HALF_ATR,
    role: str,
) -> list[dict[str, Any]]:
    if not _finite(atr) or atr <= 0:
        return []
    hw = float(half_mult) * float(atr)
    width = 2.0 * hw
    xs = [
        r
        for r in reactions
        if str(r.get("role")) == role
        and str(r.get("available_from") or "") <= session_date
        and str(r.get("reaction_date") or "") in lookback_dates
        and _finite(r.get("price"))
    ]
    if not xs:
        return []
    xs = sorted(xs, key=lambda r: (float(r["price"]), str(r["reaction_date"])))
    groups: list[list[dict[str, Any]]] = []
    cur = [xs[0]]
    for r in xs[1:]:
        if float(r["price"]) - float(cur[0]["price"]) <= width:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    zones = []
    for g in groups:
        prices = [float(x["price"]) for x in g]
        days = sorted({str(x["reaction_date"]) for x in g})
        center = _median(prices)
        n_days = len(days)
        second = days[1] if n_days >= MIN_TOUCHES else None
        activated = None
        if second:
            hit = next(x for x in g if str(x["reaction_date"]) == second)
            activated = str(hit.get("available_from") or "")
        active = bool(activated and activated <= session_date and n_days >= MIN_TOUCHES)
        first = min(g, key=lambda x: str(x["reaction_date"]))
        last = max(g, key=lambda x: str(x["reaction_date"]))
        touch_bucket = "1" if n_days < 2 else ("2" if n_days == 2 else ("3" if n_days == 3 else "4+"))
        zones.append(
            {
                "zone_id": f"{g[0].get('symbol') or ''}:{role}:{first['reaction_date']}:{round(center, 2)}",
                "role": role,
                "center": center,
                "lo": center - hw,
                "hi": center + hw,
                "half_width": hw,
                "atr": atr,
                "half_mult": half_mult,
                "touch_count": len(g),
                "distinct_touch_days": n_days,
                "touch_bucket": touch_bucket,
                "first_reaction_date": first["reaction_date"],
                "last_reaction_date": last["reaction_date"],
                "ZONE_ACTIVATED_AT": activated,
                "active": active,
                "control_single_touch": n_days == 1,
                "members": g,
                "volume_at_reactions": float(sum(float(x.get("volume") or 0) for x in g)),
                "reaction_magnitude": float(np.mean([float(x.get("rejection_frac") or 0) for x in g])),
                "symbol_specific": True,
                "future_touches_used": False,
            }
        )
    return zones


def snapshot_zones(
    *,
    symbol: str,
    reactions: list[dict[str, Any]],
    atr: float,
    session_date: str,
    lookback_dates: list[str],
    half_mult: float = ZONE_HALF_ATR,
) -> dict[str, Any]:
    lb = set(lookback_dates[-LOOKBACK_DAYS:]) if lookback_dates else set()
    tagged = [{**r, "symbol": symbol} for r in reactions]
    res = cluster_role(tagged, atr=atr, session_date=session_date, lookback_dates=lb, half_mult=half_mult, role="RESISTANCE")
    sup = cluster_role(tagged, atr=atr, session_date=session_date, lookback_dates=lb, half_mult=half_mult, role="SUPPORT")
    active_res = [z for z in res if z["active"]]
    active_sup = [z for z in sup if z["active"]]
    single_res = [z for z in res if z["control_single_touch"]]
    return {
        "atr": atr,
        "resistance_active": active_res,
        "support_active": active_sup,
        "resistance_single": single_res,
        "support_single": [z for z in sup if z["control_single_touch"]],
        "resistance_all": res,
        "support_all": sup,
        "n_res_2": sum(1 for z in active_res if z["touch_bucket"] == "2"),
        "n_res_3": sum(1 for z in active_res if z["touch_bucket"] == "3"),
        "n_res_4": sum(1 for z in active_res if z["touch_bucket"] == "4+"),
        "n_sup_2": sum(1 for z in active_sup if z["touch_bucket"] == "2"),
        "n_sup_3": sum(1 for z in active_sup if z["touch_bucket"] == "3"),
        "n_sup_4": sum(1 for z in active_sup if z["touch_bucket"] == "4+"),
    }


def diagnostic_counts(reactions: list[dict[str, Any]], atr: float, session_date: str, lookback_dates: list[str], symbol: str) -> dict[str, int]:
    out: dict[str, int] = {}
    from research.multi_touch_daily_zone_1m_price_action_v1 import LOOKBACK_DIAG, ZONE_HALF_ATR_DIAG

    for hm in (ZONE_HALF_ATR,) + ZONE_HALF_ATR_DIAG:
        for lb_n in (LOOKBACK_DAYS,) + LOOKBACK_DIAG:
            lb = lookback_dates[-lb_n:] if lookback_dates else []
            snap = snapshot_zones(symbol=symbol, reactions=reactions, atr=atr, session_date=session_date, lookback_dates=lb, half_mult=hm)
            key = f"hw{hm}_lb{lb_n}"
            out[key + "_res"] = len(snap["resistance_active"])
            out[key + "_sup"] = len(snap["support_active"])
    return out

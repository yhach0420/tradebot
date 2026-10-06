"""A1 prepositioned touch, A2 confirmed rejection, C1 confirmed retest-hold. BAR_START. No same-bar entry."""
from __future__ import annotations

from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1.direction import sign_for
from research.support_resistance_matched_separation_not_a_strategy_v1 import A1_CLASSIFICATION
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i, signed_from_entry, signed_from_event


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def a1_passive(ep: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    res = bool(ep.get("as_resistance"))
    lo, hi = float(ep["lo"]), float(ep["hi"])
    limit = lo if res else hi
    t_ft = ep.get("first_test_time")
    i = rec["idx"].get(str(t_ft)) if t_ft else None
    first_t = rec["t"][0] if rec.get("t") else None
    gap_through = False
    if i is not None:
        o = rec["o"][int(i)]
        if _finite(o):
            gap_through = bool((res and float(o) > hi) or ((not res) and float(o) < lo))
    fillable = False
    fill_price = None
    if i is not None and not gap_through:
        h, l = float(rec["h"][int(i)]), float(rec["l"][int(i)])
        fillable = bool(l <= limit <= h)
        fill_price = float(limit) if fillable else None
    sign = sign_for("A", resistance=res)
    j = next_entry_i(rec, int(i)) if i is not None and fillable else None
    path = signed_from_entry(rec, j, sign) if fillable else signed_from_entry(rec, None, sign)
    path["decision_t"] = "PRE_OPEN"
    path["same_bar_entry"] = False
    return {
        "classification": A1_CLASSIFICATION,
        "actual_fill_proven": False,
        "zone_known_at": ep.get("zone_activated_at"),
        "order_placed_at": "PRE_OPEN",
        "order_placed_before_first_bar": True,
        "limit_price": limit,
        "first_fill_eligible_bar": t_ft,
        "fill_bar": t_ft if fillable else None,
        "fill_price": fill_price,
        "gap_through": gap_through,
        "fillable_approx": fillable,
        "research_direction": "SELL_SHORT" if res else "BUY",
        "same_bar_placement": False,
        "path": path,
        "first_session_bar": first_t,
    }


def a2_confirmed(ep: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    res = bool(ep.get("as_resistance"))
    confirmed = bool(ep.get("rejection")) and not bool(ep.get("break"))
    t = ep.get("rejection_time") if confirmed else None
    i = rec["idx"].get(str(t)) if t else None
    j = next_entry_i(rec, int(i)) if i is not None else None
    sign = sign_for("A", resistance=res)
    path = signed_from_entry(rec, j, sign)
    path["decision_t"] = t
    path["same_bar_entry"] = bool(j is not None and t is not None and rec["t"][j] == t)
    lost = "BREAK" if ep.get("break") else ("UNRESOLVED" if not confirmed else None)
    ft = ep.get("first_test_time")
    i_ft = rec["idx"].get(str(ft)) if ft else None
    first_path = signed_from_event(rec, int(i_ft), sign) if i_ft is not None else signed_from_entry(rec, None, sign)
    return {
        "confirmed": confirmed,
        "decision_t": t,
        "entry_t": path.get("entry_t"),
        "same_bar_entry": path.get("same_bar_entry"),
        "event_loss": lost,
        "bars_first_test_to_confirm": (
            (int(i) - int(i_ft)) if i is not None and i_ft is not None else None
        ),
        "path": path,
        "first_test_path": first_path,
    }


def c1_hold(ep: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    res = bool(ep.get("as_resistance"))
    held = bool(ep.get("retest_hold"))
    t = ep.get("retest_hold_time") if held else None
    i = rec["idx"].get(str(t)) if t else None
    j = next_entry_i(rec, int(i)) if i is not None else None
    sign = sign_for("C", resistance=res)
    path = signed_from_entry(rec, j, sign)
    path["decision_t"] = t
    path["same_bar_entry"] = bool(j is not None and t is not None and rec["t"][j] == t)
    hold_path = signed_from_event(rec, int(i), sign) if i is not None else signed_from_entry(rec, None, sign)
    return {
        "confirmed": held,
        "decision_t": t,
        "entry_t": path.get("entry_t"),
        "same_bar_entry": path.get("same_bar_entry"),
        "path": path,
        "hold_bar_path": hold_path,
    }

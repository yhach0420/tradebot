"""1-minute causal state vs an active daily zone. ACCEPT/RETEST timestamps are not moved backward."""
from __future__ import annotations

from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1 import REFRACTORY_MIN

KIND_BUCKET = {
    "APPROACH_ZONE": "approach",
    "ENTER_ZONE": "enter",
    "REJECT_FROM_RESISTANCE": "reject",
    "REJECT_FROM_SUPPORT": "reject",
    "BREAK_ABOVE_ZONE": "break",
    "BREAK_BELOW_ZONE": "break",
    "ACCEPT1_ABOVE": "accept",
    "ACCEPT2_ABOVE": "accept",
    "ACCEPT1_BELOW": "accept",
    "ACCEPT2_BELOW": "accept",
    "RETEST_ZONE": "retest",
    "RETEST_HOLD": "retest_hold",
    "FAIL_BACK_BELOW": "failed_retest",
    "FAIL_BACK_ABOVE": "failed_retest",
    "RESISTANCE_BROKEN": "break",
    "RESISTANCE_TO_SUPPORT_FLIP": "flip",
    "SUPPORT_BROKEN": "break",
    "SUPPORT_TO_RESISTANCE_FLIP": "flip",
    "SUPPORT_FALSE_BREAK_RECLAIM": "reclaim",
    "PDH_BREAK_ABOVE": "pdh_break",
}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def new_zone_state(zone: dict[str, Any]) -> dict[str, Any]:
    return {
        "zone": zone,
        "role": zone["role"],
        "lo": float(zone["lo"]),
        "hi": float(zone["hi"]),
        "approached": False,
        "entered": False,
        "broke_above": False,
        "broke_below": False,
        "accept_above": 0,
        "accept_below": 0,
        "waiting_retest": False,
        "flipped": False,
        "failed_below": False,
        "last": {},
        "break_above_bar": None,
        "break_below_bar": None,
        "bars_inside": 0,
    }


def _allow(st: dict[str, Any], kind: str, tm: int) -> bool:
    last = st["last"].get(kind)
    if last is not None and int(tm) - int(last) <= int(REFRACTORY_MIN):
        return False
    st["last"][kind] = int(tm)
    return True


def _emit(st: dict[str, Any], kind: str, *, tm: int, feature_bar: str, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not _allow(st, kind, tm):
        return None
    z = st["zone"]
    row = {
        "event_kind": kind,
        "bucket": KIND_BUCKET.get(kind, "other"),
        "feature_bar": feature_bar,
        "zone_id": z.get("zone_id"),
        "role": st["role"],
        "zone_lo": st["lo"],
        "zone_hi": st["hi"],
        "zone_center": z.get("center"),
        "touch_count": z.get("touch_count"),
        "distinct_touch_days": z.get("distinct_touch_days"),
        "touch_bucket": z.get("touch_bucket"),
        "ZONE_ACTIVATED_AT": z.get("ZONE_ACTIVATED_AT"),
        "level_value": st["hi"] if st["role"] == "RESISTANCE" else st["lo"],
        "control_single_touch": bool(z.get("control_single_touch")),
        "future_dependent": False,
        "retroactive_timestamp": False,
        "break_feature_bar": extra.get("break_feature_bar") if extra else st.get("break_above_bar") or st.get("break_below_bar"),
        "bars_inside": st["bars_inside"],
        "flipped": st["flipped"],
    }
    if extra:
        row.update(extra)
    return row


def step_zone(
    st: dict[str, Any],
    *,
    c: float,
    h: float,
    l: float,
    prev_c: float,
    tm: int,
    feature_bar: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lo, hi = st["lo"], st["hi"]
    if not _finite(c) or not _finite(lo) or not _finite(hi) or hi <= lo:
        return out
    in_zone = l <= hi and h >= lo
    if in_zone:
        st["bars_inside"] += 1
    role = st["role"]

    if (not st["approached"]) and _finite(prev_c):
        if role == "RESISTANCE" and prev_c < lo and c >= lo - (hi - lo) and c < lo:
            ev = _emit(st, "APPROACH_ZONE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
                st["approached"] = True
        if role == "SUPPORT" and prev_c > hi and c <= hi + (hi - lo) and c > hi:
            ev = _emit(st, "APPROACH_ZONE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
                st["approached"] = True

    if in_zone and not st["entered"]:
        ev = _emit(st, "ENTER_ZONE", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        st["entered"] = True

    crossed_up = _finite(prev_c) and prev_c <= hi < c
    crossed_dn = _finite(prev_c) and prev_c >= lo > c

    if role == "RESISTANCE":
        if crossed_up:
            ev = _emit(st, "BREAK_ABOVE_ZONE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            evb = _emit(st, "RESISTANCE_BROKEN", tm=tm, feature_bar=feature_bar)
            if evb:
                out.append(evb)
            st["broke_above"] = True
            st["accept_above"] = 0
            st["waiting_retest"] = True
            st["break_above_bar"] = feature_bar
        elif st["entered"] and (not st["broke_above"]) and _finite(c) and c < lo:
            ev = _emit(st, "REJECT_FROM_RESISTANCE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["entered"] = False

        if st["broke_above"] and not crossed_up:
            if c > hi:
                st["accept_above"] += 1
                if st["accept_above"] == 1:
                    ev = _emit(st, "ACCEPT1_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                    if ev:
                        out.append(ev)
                elif st["accept_above"] == 2:
                    ev = _emit(st, "ACCEPT2_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                    if ev:
                        out.append(ev)
                    evf = _emit(st, "RESISTANCE_TO_SUPPORT_FLIP", tm=tm, feature_bar=feature_bar)
                    if evf:
                        out.append(evf)
                    st["waiting_retest"] = True
                    st["flipped"] = True
                    st["broke_above"] = False
            else:
                st["broke_above"] = False
                st["failed_below"] = True
                st["accept_above"] = 0

        if st["waiting_retest"] or st["flipped"]:
            if in_zone and c >= lo:
                ev = _emit(st, "RETEST_ZONE", tm=tm, feature_bar=feature_bar)
                if ev:
                    out.append(ev)
                if c > lo:
                    evh = _emit(st, "RETEST_HOLD", tm=tm, feature_bar=feature_bar)
                    if evh:
                        out.append(evh)
                    st["waiting_retest"] = False
            if c < lo:
                ev = _emit(st, "FAIL_BACK_BELOW", tm=tm, feature_bar=feature_bar)
                if ev:
                    out.append(ev)
                st["waiting_retest"] = False
                st["flipped"] = False

    if role == "SUPPORT" and not st["flipped"]:
        if crossed_dn:
            ev = _emit(st, "BREAK_BELOW_ZONE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            evb = _emit(st, "SUPPORT_BROKEN", tm=tm, feature_bar=feature_bar)
            if evb:
                out.append(evb)
            st["broke_below"] = True
            st["accept_below"] = 0
            st["break_below_bar"] = feature_bar
        elif st["entered"] and (not st["broke_below"]) and c > hi:
            ev = _emit(st, "REJECT_FROM_SUPPORT", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["entered"] = False

        if st["broke_below"] and not crossed_dn:
            if c < lo:
                st["accept_below"] += 1
                if st["accept_below"] == 1:
                    ev = _emit(st, "ACCEPT1_BELOW", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
                    if ev:
                        out.append(ev)
                elif st["accept_below"] == 2:
                    ev = _emit(st, "ACCEPT2_BELOW", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
                    if ev:
                        out.append(ev)
                    evf = _emit(st, "SUPPORT_TO_RESISTANCE_FLIP", tm=tm, feature_bar=feature_bar)
                    if evf:
                        out.append(evf)
                    st["broke_below"] = False
            else:
                ev = _emit(st, "SUPPORT_FALSE_BREAK_RECLAIM", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
                if ev:
                    out.append(ev)
                st["broke_below"] = False
                st["failed_below"] = True
                st["accept_below"] = 0
    return out

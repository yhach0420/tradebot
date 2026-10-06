"""Causal first-event state machine. No future cluster, no retroactive timestamps."""
from __future__ import annotations

from typing import Any

from research.reference_level_1m_price_action_discovery_v1 import APPROACH_BPS, REFRACTORY_MIN
from research.reference_level_1m_price_action_discovery_v1.levels import dist_bps

KIND_BUCKET = {
    "APPROACH_FROM_BELOW": "approach",
    "APPROACH_FROM_ABOVE": "approach",
    "TOUCH": "touch",
    "BREAK_ABOVE": "break",
    "BREAK_BELOW": "break",
    "SESSION_HIGH_EXTENSION": "break",
    "SESSION_LOW_EXTENSION": "break",
    "ACCEPT1_ABOVE": "accept",
    "ACCEPT2_ABOVE": "accept",
    "ACCEPT1_BELOW": "accept",
    "ACCEPT2_BELOW": "accept",
    "REJECT_FROM_BELOW": "reject",
    "REJECT_FROM_ABOVE": "reject",
    "RETEST_HOLD_ABOVE": "retest",
    "RETEST_HOLD_BELOW": "retest",
    "RETEST_FAIL_ABOVE": "retest",
    "RETEST_FAIL_BELOW": "retest",
    "RECLAIM_ABOVE": "reclaim",
    "RECLAIM_BELOW": "reclaim",
    "FAILED_BREAK_ABOVE": "break_failure",
    "FAILED_BREAK_BELOW": "break_failure",
    "GAP_UP": "gap_open",
    "GAP_DOWN": "gap_open",
    "GAP_UP_HOLD_OR15": "gap_hold",
    "GAP_DOWN_HOLD_OR15": "gap_hold",
    "GAP_UP_PARTIAL_FILL": "gap_partial",
    "GAP_DOWN_PARTIAL_FILL": "gap_partial",
    "GAP_UP_FULL_FILL": "gap_full",
    "GAP_DOWN_FULL_FILL": "gap_full",
    "GAP_FILL_RECLAIM": "reclaim",
    "GAP_FILL_FAILURE": "break_failure",
    "GAP_DOWN_FILL_RECLAIM": "reclaim",
    "GAP_DOWN_FILL_FAILURE": "break_failure",
    "VWAP_RECLAIM": "reclaim",
    "VWAP_LOSS": "break",
}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f != 0.0


def new_state(level_id: str, family: str, value: float, *, moving: bool = False) -> dict[str, Any]:
    return {
        "level_id": level_id,
        "family": family,
        "value": float(value) if _finite(value) else float("nan"),
        "moving": moving,
        "available": _finite(value) and not moving,
        "broke_above": False,
        "broke_below": False,
        "accept_above": 0,
        "accept_below": 0,
        "waiting_retest_above": False,
        "waiting_retest_below": False,
        "failed_above": False,
        "failed_below": False,
        "approached": False,
        "last": {},
        "break_above_bar": None,
        "break_below_bar": None,
        "prev_vw": float("nan"),
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
    row = {
        "level_id": st["level_id"],
        "family": st["family"],
        "event_kind": kind,
        "bucket": KIND_BUCKET.get(kind, "other"),
        "level_value": st["value"],
        "feature_bar": feature_bar,
        "break_feature_bar": extra.get("break_feature_bar") if extra else st.get("break_above_bar") or st.get("break_below_bar"),
        "future_dependent": False,
        "retroactive_timestamp": False,
        "first_event_refractory": True,
    }
    if extra:
        row.update(extra)
    return row


def step_static(
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
    L = st["value"]
    if not st.get("available") or not _finite(L) or not _finite(c):
        return out
    touch = _finite(h) and _finite(l) and l <= L <= h
    d = dist_bps(c, L)
    if (not st["approached"]) and d == d and 0 < abs(d) <= APPROACH_BPS and not touch:
        kind = "APPROACH_FROM_BELOW" if c < L else "APPROACH_FROM_ABOVE"
        ev = _emit(st, kind, tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
            st["approached"] = True
    if touch:
        ev = _emit(st, "TOUCH", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)

    crossed_up = _finite(prev_c) and prev_c <= L < c
    crossed_dn = _finite(prev_c) and prev_c >= L > c

    if crossed_up:
        ev = _emit(st, "BREAK_ABOVE", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        st["broke_above"] = True
        st["broke_below"] = False
        st["accept_above"] = 0
        st["accept_below"] = 0
        st["waiting_retest_above"] = False
        st["failed_above"] = False
        st["break_above_bar"] = feature_bar
        if st["failed_below"]:
            ev = _emit(st, "RECLAIM_ABOVE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["failed_below"] = False
    elif crossed_dn:
        ev = _emit(st, "BREAK_BELOW", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        st["broke_below"] = True
        st["broke_above"] = False
        st["accept_below"] = 0
        st["accept_above"] = 0
        st["waiting_retest_below"] = False
        st["failed_below"] = False
        st["break_below_bar"] = feature_bar
        if st["failed_above"]:
            ev = _emit(st, "RECLAIM_BELOW", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["failed_above"] = False
    else:
        if touch and _finite(prev_c):
            if prev_c < L and c <= L:
                ev = _emit(st, "REJECT_FROM_BELOW", tm=tm, feature_bar=feature_bar)
                if ev:
                    out.append(ev)
            if prev_c > L and c >= L:
                ev = _emit(st, "REJECT_FROM_ABOVE", tm=tm, feature_bar=feature_bar)
                if ev:
                    out.append(ev)

    if st["broke_above"] and not crossed_up:
        if c > L:
            st["accept_above"] += 1
            if st["accept_above"] == 1:
                ev = _emit(st, "ACCEPT1_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                if ev:
                    out.append(ev)
            elif st["accept_above"] == 2:
                ev = _emit(st, "ACCEPT2_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                if ev:
                    out.append(ev)
                st["waiting_retest_above"] = True
                st["broke_above"] = False
        else:
            ev = _emit(st, "FAILED_BREAK_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
            if ev:
                out.append(ev)
            st["broke_above"] = False
            st["failed_above"] = True
            st["accept_above"] = 0
            st["waiting_retest_above"] = False

    if st["broke_below"] and not crossed_dn:
        if c < L:
            st["accept_below"] += 1
            if st["accept_below"] == 1:
                ev = _emit(st, "ACCEPT1_BELOW", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
                if ev:
                    out.append(ev)
            elif st["accept_below"] == 2:
                ev = _emit(st, "ACCEPT2_BELOW", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
                if ev:
                    out.append(ev)
                st["waiting_retest_below"] = True
                st["broke_below"] = False
        else:
            ev = _emit(st, "FAILED_BREAK_BELOW", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_below_bar"]})
            if ev:
                out.append(ev)
            st["broke_below"] = False
            st["failed_below"] = True
            st["accept_below"] = 0
            st["waiting_retest_below"] = False

    if st["waiting_retest_above"] and st["accept_above"] >= 2:
        if touch and c > L:
            ev = _emit(st, "RETEST_HOLD_ABOVE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["waiting_retest_above"] = False
        elif c <= L:
            ev = _emit(st, "RETEST_FAIL_ABOVE", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["waiting_retest_above"] = False

    if st["waiting_retest_below"] and st["accept_below"] >= 2:
        if touch and c < L:
            ev = _emit(st, "RETEST_HOLD_BELOW", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["waiting_retest_below"] = False
        elif c >= L:
            ev = _emit(st, "RETEST_FAIL_BELOW", tm=tm, feature_bar=feature_bar)
            if ev:
                out.append(ev)
            st["waiting_retest_below"] = False
    return out


def step_csh_csl(
    st: dict[str, Any],
    *,
    extreme: float,
    tm: int,
    feature_bar: str,
    kind: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    L = st["value"]
    if not st.get("available") or not _finite(L) or not _finite(extreme):
        return out
    if kind == "SESSION_HIGH_EXTENSION" and extreme > L:
        ev = _emit(st, kind, tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        st["value"] = float(extreme)
    if kind == "SESSION_LOW_EXTENSION" and extreme < L:
        ev = _emit(st, kind, tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        st["value"] = float(extreme)
    return out


def step_vwap(
    st: dict[str, Any],
    *,
    c: float,
    vw: float,
    prev_c: float,
    tm: int,
    feature_bar: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    st["value"] = float(vw) if _finite(vw) else float("nan")
    st["available"] = _finite(vw)
    if not st["available"] or not _finite(c):
        st["prev_vw"] = float(vw) if _finite(vw) else float("nan")
        return out
    prev_vw = st.get("prev_vw")
    above = c > vw
    prev_below = _finite(prev_c) and _finite(prev_vw) and prev_c <= prev_vw
    prev_above = _finite(prev_c) and _finite(prev_vw) and prev_c > prev_vw
    if above and prev_below:
        ev = _emit(st, "VWAP_RECLAIM", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        ev2 = _emit(st, "BREAK_ABOVE", tm=tm, feature_bar=feature_bar)
        if ev2:
            out.append(ev2)
        st["broke_above"] = True
        st["accept_above"] = 0
        st["break_above_bar"] = feature_bar
        st["failed_below"] = False
    if (not above) and prev_above:
        ev = _emit(st, "VWAP_LOSS", tm=tm, feature_bar=feature_bar)
        if ev:
            out.append(ev)
        ev2 = _emit(st, "BREAK_BELOW", tm=tm, feature_bar=feature_bar)
        if ev2:
            out.append(ev2)
    elif st["broke_above"] and not (above and prev_below):
        if above:
            st["accept_above"] += 1
            if st["accept_above"] == 1:
                ev = _emit(st, "ACCEPT1_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                if ev:
                    out.append(ev)
            elif st["accept_above"] == 2:
                ev = _emit(st, "ACCEPT2_ABOVE", tm=tm, feature_bar=feature_bar, extra={"break_feature_bar": st["break_above_bar"]})
                if ev:
                    out.append(ev)
                st["broke_above"] = False
        else:
            st["broke_above"] = False
            st["failed_above"] = True
            st["accept_above"] = 0
    st["prev_vw"] = float(vw)
    return out


def new_gap_state(*, side: str | None, session_open: float, pdc: float) -> dict[str, Any]:
    return {
        "side": side,
        "open": session_open,
        "pdc": pdc,
        "partial": False,
        "full": False,
        "hold_or15": False,
        "last": {},
        "level_id": "GAP_UP_PDC" if side == "GAP_UP" else "GAP_DN_PDC",
        "family": "gap_structure",
        "value": pdc,
        "accept_through": 0,
        "filled_bar": None,
    }


def step_gap(
    gs: dict[str, Any],
    *,
    c: float,
    h: float,
    l: float,
    tm: int,
    feature_bar: str,
    first_bar: bool,
    sess_high: float,
    sess_low: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    side = gs.get("side")
    if not side:
        return out
    o = gs["open"]
    pdc = gs["pdc"]
    st = gs

    def emit(kind: str, extra: dict[str, Any] | None = None) -> None:
        ev = _emit(st, kind, tm=tm, feature_bar=feature_bar, extra=extra)
        if ev:
            ev["level_id"] = gs["level_id"]
            ev["family"] = "gap_structure"
            ev["level_value"] = pdc
            out.append(ev)

    if first_bar:
        emit("GAP_UP" if side == "GAP_UP" else "GAP_DOWN")
    if side == "GAP_UP":
        if (not gs["partial"]) and _finite(l) and _finite(o) and l < o and l > pdc:
            gs["partial"] = True
            emit("GAP_UP_PARTIAL_FILL")
        if (not gs["full"]) and _finite(l) and _finite(pdc) and l <= pdc:
            gs["full"] = True
            gs["partial"] = True
            gs["filled_bar"] = feature_bar
            gs["accept_through"] = 0
            emit("GAP_UP_FULL_FILL")
        if gs["full"]:
            if _finite(c) and c <= pdc:
                gs["accept_through"] += 1
                if gs["accept_through"] == 2:
                    emit("GAP_FILL_FAILURE")
            elif _finite(c) and _finite(pdc) and c > pdc:
                emit("GAP_FILL_RECLAIM")
                gs["full"] = False
                gs["accept_through"] = 0
        if (not gs["hold_or15"]) and feature_bar >= "09:15" and (not gs["full"]):
            gs["hold_or15"] = True
            if _finite(sess_low) and _finite(o) and sess_low >= o:
                emit("GAP_UP_HOLD_OR15")
    else:
        if (not gs["partial"]) and _finite(h) and _finite(o) and h > o and h < pdc:
            gs["partial"] = True
            emit("GAP_DOWN_PARTIAL_FILL")
        if (not gs["full"]) and _finite(h) and _finite(pdc) and h >= pdc:
            gs["full"] = True
            gs["partial"] = True
            gs["filled_bar"] = feature_bar
            gs["accept_through"] = 0
            emit("GAP_DOWN_FULL_FILL")
        if gs["full"]:
            if _finite(c) and c >= pdc:
                gs["accept_through"] += 1
                if gs["accept_through"] == 2:
                    emit("GAP_DOWN_FILL_FAILURE")
            elif _finite(c) and c < pdc:
                emit("GAP_DOWN_FILL_RECLAIM")
                gs["full"] = False
                gs["accept_through"] = 0
        if (not gs["hold_or15"]) and feature_bar >= "09:15" and (not gs["full"]):
            gs["hold_or15"] = True
            if _finite(sess_high) and _finite(o) and sess_high <= o:
                emit("GAP_DOWN_HOLD_OR15")
    return out

"""Causal context features at completed event bar. No indicator catalog. No future bars."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.native_participation_x_sr_context_discovery_v1.native import close_location
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _dist_atr(px: float, bound: float, atr: float) -> float:
    if not _finite(px) or not _finite(bound) or not _finite(atr) or float(atr) <= 0:
        return float("nan")
    return abs(float(px) - float(bound)) / float(atr)


def sr_context(
    *,
    rec: dict[str, Any],
    i: int,
    atr: float,
    snap: dict[str, Any],
    eps: list[dict[str, Any]],
    t: str,
) -> dict[str, Any]:
    c = float(rec["c"][i]) if _finite(rec["c"][i]) else float("nan")
    h, l = float(rec["h"][i]), float(rec["l"][i])
    sups = list(snap.get("support_active") or [])
    ress = list(snap.get("resistance_active") or [])
    d_sup = float("nan")
    d_res = float("nan")
    if _finite(c):
        below = []
        for z in sups:
            hi = float(z["hi"])
            if hi <= c or (float(z["lo"]) <= c <= hi):
                below.append((max(0.0, c - hi), z))
        if below:
            below.sort(key=lambda x: x[0])
            d_sup = _dist_atr(c, float(below[0][1]["hi"]), atr)
        above = []
        for z in ress:
            lo = float(z["lo"])
            if lo >= c or (lo <= c <= float(z["hi"])):
                above.append((max(0.0, lo - c), z))
        if above:
            above.sort(key=lambda x: x[0])
            d_res = _dist_atr(c, float(above[0][1]["lo"]), atr)
    inside = 0.0
    for z in sups + ress:
        if l <= float(z["hi"]) and h >= float(z["lo"]):
            inside = 1.0
            break
    breaking = 0.0
    if _finite(c):
        for z in ress:
            if c > float(z["hi"]):
                breaking = 1.0
                break
        if not breaking:
            for z in sups:
                if c < float(z["lo"]):
                    breaking = 1.0
                    break
    retest = 0.0
    for ep in eps:
        if not is_primary(str(ep.get("selection_slot") or "")):
            continue
        if not ep.get("break"):
            continue
        if ep.get("retest") or ep.get("retest_hold"):
            ht = str(ep.get("retest_time") or ep.get("retest_hold_time") or "")
            if ht and ht <= t:
                retest = 1.0
                break
    return {
        "dist_support_atr": d_sup,
        "dist_resistance_atr": d_res,
        "inside_active_zone": inside,
        "breaking_active_zone": breaking,
        "post_break_retest": retest,
    }


def local_structure(rec: dict[str, Any], session_idx: list[int], pos: int, direction: str) -> dict[str, Any]:
    if pos < 20:
        return {"prior5_range_rel": float("nan"), "prior5_ret": float("nan"), "pullback_from_extreme": float("nan")}
    prev5 = session_idx[pos - 5 : pos]
    prev20 = session_idx[pos - 20 : pos]
    i = session_idx[pos]
    rngs20 = [float(rec["h"][j]) - float(rec["l"][j]) for j in prev20]
    med20 = float(np.median(rngs20)) if rngs20 else float("nan")
    win_h = max(float(rec["h"][j]) for j in prev5) if prev5 else float("nan")
    win_l = min(float(rec["l"][j]) for j in prev5) if prev5 else float("nan")
    rel = (win_h - win_l) / med20 if _finite(win_h) and _finite(win_l) and _finite(med20) and med20 > 0 else float("nan")
    c0 = rec["c"][i]
    c5 = rec["c"][prev5[0]] if prev5 else None
    pret = float(c0) / float(c5) - 1.0 if _finite(c0) and _finite(c5) and float(c5) != 0 else float("nan")
    mx = max(float(rec["h"][j]) for j in prev5) if prev5 else float("nan")
    mn = min(float(rec["l"][j]) for j in prev5) if prev5 else float("nan")
    atr_proxy = med20 if _finite(med20) and med20 > 0 else float("nan")
    if direction == "BULLISH" and _finite(c0) and _finite(mx) and _finite(atr_proxy):
        pull = (mx - float(c0)) / atr_proxy
    elif direction == "BEARISH" and _finite(c0) and _finite(mn) and _finite(atr_proxy):
        pull = (float(c0) - mn) / atr_proxy
    else:
        pull = float("nan")
    return {"prior5_range_rel": rel, "prior5_ret": pret, "pullback_from_extreme": pull}


def vwap_context(rec: dict[str, Any], i: int) -> dict[str, Any]:
    c = rec["c"][i]
    vw = rec["vw"][i]
    if not _finite(c) or not _finite(vw) or float(c) <= 0:
        return {"vwap_bps": float("nan"), "above_vwap": float("nan")}
    bps = (float(c) - float(vw)) / float(c) * 10_000.0
    return {"vwap_bps": bps, "above_vwap": 1.0 if float(c) > float(vw) else 0.0}


FEATURE_DEFINITIONS = {
    "tv_clock_pctl": "same-minute-of-session TradingValue percentile vs prior 20 completed trading days, min 10 observations; continuous; no 0.80 gate",
    "dist_support_atr": "distance to nearest active support (high bound at or below close) / ATR20; frozen detector",
    "dist_resistance_atr": "distance to nearest active resistance (low bound at or above close) / ATR20; frozen detector",
    "inside_active_zone": "event bar overlaps any active support or resistance zone",
    "breaking_active_zone": "event close through far bound of an active resistance (bull) or support (bear)",
    "post_break_retest": "salient episode already broken and a retest is known at or before event time",
    "vwap_bps": "close minus causal session VWAP in bps of close; bars [0, T] only",
    "above_vwap": "1 if close > causal session VWAP",
    "r5": "completed 5-minute clock return through event bar T; no future aggregation",
    "r15": "completed 15-minute clock return through event bar T; no future aggregation",
    "prior5_range_rel": "prior 5 completed-bar high-low range / prior-20 median bar range",
    "prior5_ret": "close[T] / close[T-5 session bars] - 1",
    "pullback_from_extreme": "distance from prior-5 extreme toward the event close, in prior-20 median range units",
    "mkt_rel": "symbol session return minus cross-sectional market median, prior completed bar only",
    "sec_rel": "symbol session return minus same-sector median, prior completed bar only",
}

__all__ = ["sr_context", "local_structure", "vwap_context", "close_location", "FEATURE_DEFINITIONS"]

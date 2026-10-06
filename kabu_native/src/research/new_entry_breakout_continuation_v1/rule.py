"""Frozen Breakout Continuation rule. No search. Close vs previous 5 highs. Volume > prior-10 median. Close > session VWAP. First-cross only."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_breakout_continuation_v1 import HIGH_WINDOW, VOLUME_WINDOW

EXACT_RULE_TEXT = (
    "P1: Close[i] > max(High[i-5], High[i-4], High[i-3], High[i-2], High[i-1]). "
    "Use completed 1m Close, not current bar High. "
    "P2: Volume[i] > median(Volume[i-10:i]) of the previous 10 completed 1m bars. No multiplier. "
    "P3: Close[i] > VWAP[i] (session VWAP through bar i). "
    "FINAL = P1 AND P2 AND P3. "
    "FIRST-CROSS: P1 is true now and P1 evaluated at i-1 is false. "
    "SIGNAL_T0 = completed 1m bar i finalize timestamp. No future bars."
)


def session_vwap(close: np.ndarray, volume: np.ndarray, vwap_num: np.ndarray | None = None) -> np.ndarray:
    n = int(close.size)
    out = np.full(n, np.nan, dtype=float)
    num = 0.0
    den = 0.0
    for i in range(n):
        vol = float(volume[i]) if volume[i] == volume[i] else 0.0
        if vol > 0:
            if vwap_num is not None and vwap_num[i] == vwap_num[i] and float(vwap_num[i]) > 0:
                num += float(vwap_num[i])
            elif close[i] == close[i]:
                num += float(close[i]) * vol
            den += vol
        if den > 0:
            out[i] = num / den
    return out


def p1_price_breakout(high: np.ndarray, close: np.ndarray, i: int, *, window: int = HIGH_WINDOW) -> bool:
    if i < int(window):
        return False
    cl = float(close[i])
    if not (cl == cl):
        return False
    prev = high[i - int(window) : i]
    if int(prev.size) != int(window) or not np.all(np.isfinite(prev)):
        return False
    return cl > float(np.max(prev))


def p2_volume_expansion(volume: np.ndarray, i: int, *, window: int = VOLUME_WINDOW) -> bool:
    if i < int(window):
        return False
    cur = float(volume[i])
    if not (cur == cur):
        return False
    prev = volume[i - int(window) : i]
    if int(prev.size) != int(window) or not np.all(np.isfinite(prev)):
        return False
    return cur > float(np.median(prev))


def p3_above_vwap(close: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    cl = float(close[i]) if close[i] == close[i] else None
    vw = float(vwap[i]) if vwap[i] == vwap[i] else None
    if cl is None or vw is None:
        return False
    return cl > vw


def signal_at(high: np.ndarray, close: np.ndarray, volume: np.ndarray, vwap: np.ndarray, i: int) -> dict[str, Any]:
    p1 = p1_price_breakout(high, close, i)
    p1_prev = p1_price_breakout(high, close, i - 1) if i >= 1 else False
    first_cross = bool(p1) and (not bool(p1_prev))
    p2 = p2_volume_expansion(volume, i)
    p3 = p3_above_vwap(close, vwap, i)
    return {
        "P1": bool(p1),
        "P1_PREV": bool(p1_prev),
        "FIRST_CROSS": bool(first_cross),
        "P2": bool(p2),
        "P3": bool(p3),
        "SIGNAL": bool(first_cross and p2 and p3),
    }

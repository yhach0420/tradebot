"""Frozen VWAP Rejection/Reclaim rule. No search. Same completed 1m bar: Low < VWAP, Close > VWAP, Close > Open. First-cross of full reclaim state."""
from __future__ import annotations

from typing import Any

import numpy as np

EXACT_RULE_TEXT = (
    "P1 VWAP UNDERCUT: Low[i] < VWAP[i]. "
    "P2 VWAP RECLAIM: Close[i] > VWAP[i]. "
    "P3 BULLISH REJECTION BAR: Close[i] > Open[i]. "
    "FINAL = P1 AND P2 AND P3. "
    "RECLAIM_STATE[i] = P1 AND P2 AND P3. "
    "FIRST EVENT: RECLAIM_STATE[i] true AND RECLAIM_STATE[i-1] false (false -> true). "
    "SIGNAL_T0 = completed 1m bar i finalize timestamp. No future bars. "
    "VWAP[i] is session-as-of VWAP at bar i finalize."
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


def _finite(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    if not (v == v):
        return None
    return v


def p1_vwap_undercut(low: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    lo = _finite(low, i)
    vw = _finite(vwap, i)
    if lo is None or vw is None:
        return False
    return lo < vw


def p2_vwap_reclaim(close: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    cl = _finite(close, i)
    vw = _finite(vwap, i)
    if cl is None or vw is None:
        return False
    return cl > vw


def p3_bullish_bar(open_: np.ndarray, close: np.ndarray, i: int) -> bool:
    op = _finite(open_, i)
    cl = _finite(close, i)
    if op is None or cl is None:
        return False
    return cl > op


def reclaim_state(open_: np.ndarray, low: np.ndarray, close: np.ndarray, vwap: np.ndarray, i: int) -> bool:
    return bool(p1_vwap_undercut(low, vwap, i) and p2_vwap_reclaim(close, vwap, i) and p3_bullish_bar(open_, close, i))


def signal_at(open_: np.ndarray, low: np.ndarray, close: np.ndarray, vwap: np.ndarray, i: int) -> dict[str, Any]:
    p1 = p1_vwap_undercut(low, vwap, i)
    p2 = p2_vwap_reclaim(close, vwap, i)
    p3 = p3_bullish_bar(open_, close, i)
    state = bool(p1 and p2 and p3)
    prev = reclaim_state(open_, low, close, vwap, i - 1) if i >= 1 else False
    first = bool(state) and (not bool(prev))
    return {
        "P1": bool(p1),
        "P2": bool(p2),
        "P3": bool(p3),
        "RECLAIM_STATE": bool(state),
        "RECLAIM_STATE_PREV": bool(prev),
        "FIRST_CROSS": bool(first),
        "SIGNAL": bool(first),
    }

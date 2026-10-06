"""Frozen Failed Breakdown Reclaim rule. No search. No first-cross. No extra filters."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_failed_breakdown_reclaim_v1 import LOW_WINDOW

EXACT_RULE_TEXT = (
    "bar i-1: Low[i-1] < min(Low[i-6], Low[i-5], Low[i-4], Low[i-3], Low[i-2]). "
    "bar i: Close[i] > High[i-1] AND Close[i] > Open[i]. "
    "SIGNAL_T0 = completed 1m bar i finalize. No first-cross. No extra conditions."
)


def _finite(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    if not (v == v):
        return None
    return v


def prior_failed_breakdown(low: np.ndarray, i: int, *, window: int = LOW_WINDOW) -> bool:
    prev = i - 1
    if prev < int(window):
        return False
    lo_prev = _finite(low, prev)
    if lo_prev is None:
        return False
    win = low[prev - int(window) : prev]
    if int(win.size) != int(window) or not np.all(np.isfinite(win)):
        return False
    return lo_prev < float(np.min(win))


def current_reclaim(open_: np.ndarray, high: np.ndarray, close: np.ndarray, i: int) -> bool:
    cl = _finite(close, i)
    op = _finite(open_, i)
    hi_prev = _finite(high, i - 1)
    if cl is None or op is None or hi_prev is None:
        return False
    return cl > hi_prev and cl > op


def signal_at(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int) -> dict[str, Any]:
    bd = prior_failed_breakdown(low, i)
    rc = current_reclaim(open_, high, close, i)
    return {
        "BREAKDOWN_PREV": bool(bd),
        "RECLAIM_NOW": bool(rc),
        "SIGNAL": bool(bd and rc),
    }

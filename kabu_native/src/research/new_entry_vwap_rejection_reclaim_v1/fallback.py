"""PRECOMMITTED fallback FAILED_BREAKDOWN_RECLAIM. Invoked only if PRIMARY is exact-duplicate or VWAP is structurally unavailable. Never after PRIMARY economic fail."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.new_entry_vwap_rejection_reclaim_v1 import FALLBACK_LOW_WINDOW

FALLBACK_RULE_TEXT = (
    "bar i-1: Low[i-1] < min(Low[i-6], Low[i-5], Low[i-4], Low[i-3], Low[i-2]). "
    "bar i: Close[i] > High[i-1] AND Close[i] > Open[i]. "
    "SIGNAL_T0 = completed 1m bar i finalize. First-cross of this combined state."
)


def _finite(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    if not (v == v):
        return None
    return v


def prior_bar_broke_prior5_low(low: np.ndarray, i: int, *, window: int = FALLBACK_LOW_WINDOW) -> bool:
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


def current_reclaims_breakdown_high(open_: np.ndarray, high: np.ndarray, close: np.ndarray, i: int) -> bool:
    cl = _finite(close, i)
    op = _finite(open_, i)
    hi_prev = _finite(high, i - 1)
    if cl is None or op is None or hi_prev is None:
        return False
    return cl > hi_prev and cl > op


def fallback_state(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int) -> bool:
    return bool(prior_bar_broke_prior5_low(low, i) and current_reclaims_breakdown_high(open_, high, close, i))


def fallback_signal_at(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int) -> dict[str, Any]:
    state = fallback_state(open_, high, low, close, i)
    prev = fallback_state(open_, high, low, close, i - 1) if i >= 1 else False
    first = bool(state) and (not bool(prev))
    return {
        "BREAKDOWN_PREV": bool(prior_bar_broke_prior5_low(low, i)),
        "RECLAIM_NOW": bool(current_reclaims_breakdown_high(open_, high, close, i)),
        "STATE": bool(state),
        "STATE_PREV": bool(prev),
        "FIRST_CROSS": bool(first),
        "SIGNAL": bool(first),
    }

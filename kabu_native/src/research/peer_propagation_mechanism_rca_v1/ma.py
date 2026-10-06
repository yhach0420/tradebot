"""Fixed 1-minute SMA5/SMA25/SMA75. Execution-context diagnostics. Not a peer signal."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.peer_propagation_mechanism_rca_v1 import SMA25, SMA5, SMA75, SMA_PERIODS


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def rolling_sma(close: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(close, dtype=float)
    out = np.full(x.shape[0], np.nan, dtype=float)
    if n <= 0 or x.size < n:
        return out
    filled = np.where(np.isfinite(x), x, 0.0)
    c = np.cumsum(filled)
    cnt = np.cumsum(np.isfinite(x).astype(float))
    tot = c[n - 1 :] - np.concatenate(([0.0], c[:-n]))
    nobs = cnt[n - 1 :] - np.concatenate(([0.0], cnt[:-n]))
    vals = tot / float(n)
    vals[nobs != float(n)] = np.nan
    out[n - 1 :] = vals
    return out


def sma_pack(session_close: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "sma5": rolling_sma(session_close, int(SMA5)),
        "sma25": rolling_sma(session_close, int(SMA25)),
        "sma75": rolling_sma(session_close, int(SMA75)),
    }


def ma_states(close: Any, sma5: Any, sma25: Any, sma75: Any, prev_sma25: Any, prev_sma75: Any, sign: int) -> dict[str, Any]:
    s = float(sign)
    stack = False
    if _finite(sma5) and _finite(sma25) and _finite(sma75):
        if s > 0:
            stack = float(sma5) > float(sma25) > float(sma75)
        else:
            stack = float(sma5) < float(sma25) < float(sma75)
    opp = False
    if _finite(sma5) and _finite(sma25) and _finite(sma75):
        if s > 0:
            opp = float(sma5) < float(sma25) < float(sma75)
        else:
            opp = float(sma5) > float(sma25) > float(sma75)
    return {
        "sma5": float(sma5) if _finite(sma5) else None,
        "sma25": float(sma25) if _finite(sma25) else None,
        "sma75": float(sma75) if _finite(sma75) else None,
        "ma_stack_aligned": bool(stack),
        "ma_stack_opposed": bool(opp),
        "ma25_side_aligned": bool(_finite(close) and _finite(sma25) and s * (float(close) - float(sma25)) > 0),
        "ma75_side_aligned": bool(_finite(close) and _finite(sma75) and s * (float(close) - float(sma75)) > 0),
        "sma25_slope_aligned": bool(_finite(sma25) and _finite(prev_sma25) and s * (float(sma25) - float(prev_sma25)) > 0),
        "sma75_slope_aligned": bool(_finite(sma75) and _finite(prev_sma75) and s * (float(sma75) - float(prev_sma75)) > 0),
        "sma_periods": list(SMA_PERIODS),
        "ma_is_execution_context_not_peer_signal": True,
    }


def aligned_reclaim(prev_c: Any, prev_sma: Any, curr_c: Any, curr_sma: Any, sign: int) -> bool:
    if not (_finite(prev_c) and _finite(prev_sma) and _finite(curr_c) and _finite(curr_sma)):
        return False
    s = float(sign)
    return bool(s * (float(prev_c) - float(prev_sma)) <= 0 and s * (float(curr_c) - float(curr_sma)) > 0)


def bar_touches(high: Any, low: Any, level: Any) -> bool:
    if not (_finite(high) and _finite(low) and _finite(level)):
        return False
    lo, hi = (float(low), float(high))
    if lo > hi:
        lo, hi = hi, lo
    return bool(lo <= float(level) <= hi)


def aligned_cross(prev_a: Any, prev_b: Any, curr_a: Any, curr_b: Any, sign: int) -> bool:
    if not (_finite(prev_a) and _finite(prev_b) and _finite(curr_a) and _finite(curr_b)):
        return False
    s = float(sign)
    return bool(s * (float(prev_a) - float(prev_b)) <= 0 and s * (float(curr_a) - float(curr_b)) > 0)


def aligned_vwap_reclaim(prev_c: Any, prev_vw: Any, curr_c: Any, curr_vw: Any, sign: int) -> bool:
    return aligned_reclaim(prev_c, prev_vw, curr_c, curr_vw, sign)

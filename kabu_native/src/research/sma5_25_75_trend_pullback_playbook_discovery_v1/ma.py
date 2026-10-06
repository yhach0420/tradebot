"""Fixed SMA5/SMA25/SMA75 on completed trading-minute closes. No EMA. No period search."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import SMA25, SMA5, SMA75, SMA_PERIODS


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


def stack_aligned(sma5: Any, sma25: Any, sma75: Any, sign: int) -> bool:
    if not (_finite(sma5) and _finite(sma25) and _finite(sma75)):
        return False
    if int(sign) > 0:
        return float(sma5) > float(sma25) > float(sma75)
    return float(sma5) < float(sma25) < float(sma75)


def side_aligned(price: Any, level: Any, sign: int) -> bool:
    return bool(_finite(price) and _finite(level) and float(sign) * (float(price) - float(level)) > 0)


def through_level(price: Any, level: Any, sign: int) -> bool:
    """Completed close through the level against DIR (bull: close < level)."""
    return bool(_finite(price) and _finite(level) and float(sign) * (float(price) - float(level)) < 0)


def aligned_reclaim(prev_c: Any, prev_sma: Any, curr_c: Any, curr_sma: Any, sign: int) -> bool:
    if not (_finite(prev_c) and _finite(prev_sma) and _finite(curr_c) and _finite(curr_sma)):
        return False
    s = float(sign)
    return bool(s * (float(prev_c) - float(prev_sma)) <= 0 and s * (float(curr_c) - float(curr_sma)) > 0)


def bar_touches(high: Any, low: Any, level: Any) -> bool:
    if not (_finite(high) and _finite(low) and _finite(level)):
        return False
    lo, hi = float(low), float(high)
    if lo > hi:
        lo, hi = hi, lo
    return bool(lo <= float(level) <= hi)


def reached_ma25(close: Any, high: Any, low: Any, sma25: Any, sign: int) -> bool:
    if bar_touches(high, low, sma25):
        return True
    return through_level(close, sma25, sign) or (not side_aligned(close, sma25, sign) and _finite(close) and _finite(sma25))


_ = SMA_PERIODS

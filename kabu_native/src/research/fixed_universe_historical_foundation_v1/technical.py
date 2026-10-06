"""Causal technical definitions only. No strategy search. No parameter grid."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.fixed_universe_historical_foundation_v1 import (
    BB_PERIOD,
    BB_SIGMA,
    EMA_PERIODS,
    RCI_PERIODS,
    RSI_PERIODS,
    SAME_CLOCK_BASELINE_N_DAYS,
    SMA_PERIODS,
)

# Feature at index i uses bars[0:i+1] inclusive. It becomes usable at i+1.
# Strategy phase may only use the frozen small period sets below.


def sma(close: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(close, dtype=float)
    n = int(x.size)
    p = int(period)
    out = np.full(n, np.nan, dtype=float)
    if n < p or p <= 0:
        return out
    c = np.cumsum(x)
    out[p - 1] = c[p - 1] / float(p)
    for i in range(p, n):
        out[i] = (c[i] - c[i - p]) / float(p)
    return out


def ema(close: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(close, dtype=float)
    n = int(x.size)
    p = int(period)
    out = np.full(n, np.nan, dtype=float)
    if n < p or p <= 0:
        return out
    alpha = 2.0 / (float(p) + 1.0)
    seed = float(np.mean(x[:p]))
    if seed != seed:
        return out
    out[p - 1] = seed
    prev = seed
    for i in range(p, n):
        v = float(x[i])
        if v != v:
            break
        prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _average_ranks(values: np.ndarray) -> np.ndarray:
    n = int(values.size)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = 0.5 * (float(i + 1) + float(j + 1))
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def rci_at(close: np.ndarray, end: int, period: int) -> float:
    p = int(period)
    if end + 1 < p or p <= 1:
        return float("nan")
    w = np.asarray(close[end + 1 - p : end + 1], dtype=float)
    if w.size != p or not np.all(np.isfinite(w)):
        return float("nan")
    time_rank = np.arange(1, p + 1, dtype=float)
    price_rank = _average_ranks(w)
    d = time_rank - price_rank
    den = float(p) * (float(p) * float(p) - 1.0)
    if den <= 0:
        return float("nan")
    return (1.0 - 6.0 * float(np.sum(d * d)) / den) * 100.0


def rci_series(close: np.ndarray, period: int) -> np.ndarray:
    n = int(np.asarray(close).size)
    out = np.full(n, np.nan, dtype=float)
    for i in range(int(period) - 1, n):
        out[i] = rci_at(close, i, period=int(period))
    return out


def rsi_wilder(close: np.ndarray, period: int = 14) -> np.ndarray:
    x = np.asarray(close, dtype=float)
    n = int(x.size)
    p = int(period)
    out = np.full(n, np.nan, dtype=float)
    if n <= p or p <= 0:
        return out
    delta = np.diff(x)
    gain = np.where(delta > 0.0, delta, 0.0)
    loss = np.where(delta < 0.0, -delta, 0.0)
    avg_g = float(np.mean(gain[:p]))
    avg_l = float(np.mean(loss[:p]))
    if avg_l == 0.0:
        out[p] = 100.0 if avg_g > 0.0 else 0.0
    else:
        rs = avg_g / avg_l
        out[p] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(p, n - 1):
        avg_g = (avg_g * (p - 1) + float(gain[i])) / float(p)
        avg_l = (avg_l * (p - 1) + float(loss[i])) / float(p)
        if avg_l == 0.0:
            out[i + 1] = 100.0 if avg_g > 0.0 else 0.0
        else:
            rs = avg_g / avg_l
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out


def bollinger(close: np.ndarray, period: int = BB_PERIOD, sigma: float = BB_SIGMA) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = sma(close, int(period))
    x = np.asarray(close, dtype=float)
    n = int(x.size)
    p = int(period)
    sd = np.full(n, np.nan, dtype=float)
    if n >= p and p > 0:
        for i in range(p - 1, n):
            w = x[i + 1 - p : i + 1]
            if np.all(np.isfinite(w)):
                sd[i] = float(np.std(w, ddof=0))
    return mid, mid + float(sigma) * sd, mid - float(sigma) * sd


def rolling_high(high: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(high, dtype=float)
    n = int(x.size)
    p = int(period)
    out = np.full(n, np.nan, dtype=float)
    if n < p or p <= 0:
        return out
    for i in range(p - 1, n):
        out[i] = float(np.max(x[i + 1 - p : i + 1]))
    return out


def rolling_low(low: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(low, dtype=float)
    n = int(x.size)
    p = int(period)
    out = np.full(n, np.nan, dtype=float)
    if n < p or p <= 0:
        return out
    for i in range(p - 1, n):
        out[i] = float(np.min(x[i + 1 - p : i + 1]))
    return out


def session_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, trading_value: np.ndarray | None = None) -> np.ndarray:
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    n = int(c.size)
    out = np.full(n, np.nan, dtype=float)
    num = 0.0
    den = 0.0
    va = None if trading_value is None else np.asarray(trading_value, dtype=float)
    for i in range(n):
        if va is not None and np.isfinite(va[i]) and np.isfinite(v[i]) and v[i] > 0:
            num += float(va[i])
            den += float(v[i])
        else:
            tp = (float(h[i]) + float(l[i]) + float(c[i])) / 3.0
            if np.isfinite(tp) and np.isfinite(v[i]) and v[i] > 0:
                num += tp * float(v[i])
                den += float(v[i])
        if den > 0:
            out[i] = num / den
    return out


def relative_volume(volume: np.ndarray, period: int) -> np.ndarray:
    base = sma(volume, int(period))
    v = np.asarray(volume, dtype=float)
    out = np.full(v.size, np.nan, dtype=float)
    for i in range(int(v.size)):
        b = float(base[i]) if i < base.size else float("nan")
        if b == b and b > 0 and v[i] == v[i]:
            out[i] = float(v[i]) / b
    return out


def same_clock_baseline(prior_same_clock: np.ndarray, n_days: int) -> float:
    """Median of the last n_days same-clock observations. Prior days only."""
    x = np.asarray(prior_same_clock, dtype=float)
    n = int(n_days)
    if n not in SAME_CLOCK_BASELINE_N_DAYS:
        raise ValueError("n_days not in frozen SAME_CLOCK_BASELINE_N_DAYS")
    if x.size < n:
        return float("nan")
    w = x[-n:]
    if not np.all(np.isfinite(w)):
        return float("nan")
    return float(np.median(w))


def feature_usable_index(computed_at_i: int) -> int:
    """Bar-i feature may be used from index i+1 (next bar)."""
    return int(computed_at_i) + 1


def library_spec() -> dict[str, Any]:
    return {
        "ready": True,
        "strategy_search": False,
        "periods_frozen_small_set": True,
        "SMA_PERIODS": list(SMA_PERIODS),
        "EMA_PERIODS": list(EMA_PERIODS),
        "RCI_PERIODS": list(RCI_PERIODS),
        "RSI_PERIODS": list(RSI_PERIODS),
        "BB_PERIOD": int(BB_PERIOD),
        "BB_SIGMA": float(BB_SIGMA),
        "SAME_CLOCK_BASELINE_N_DAYS": list(SAME_CLOCK_BASELINE_N_DAYS),
        "causal_rule": "feature_at_i_uses_bars_through_i_only; usable_at_i_plus_1",
        "same_bar_close_entry": False,
        "rci_local": True,
        "vwap": "session_cumsum_Va_over_Vo_else_typical_price",
        "volume": "bar_Vo",
        "trading_value": "bar_Va_else_C_times_Vo",
        "relative_volume": "Vo_over_SMA_Vo",
        "same_clock_volume": "Vo_at_HHMM_vs_median_of_prior_N_days_same_HHMM",
        "breakout_def": "close_i_gt_max_high_over_prior_n_excluding_current_optional; entry_not_on_bar_i",
        "pullback_reclaim_def": "price_returns_to_then_reclaims_a_completed_level; no_grid",
        "families_not_searched": True,
    }


def self_check() -> dict[str, Any]:
    up = np.arange(1.0, 10.0, dtype=float)
    dn = np.arange(9.0, 0.0, -1.0, dtype=float)
    r_up = rci_at(up, 8, 9)
    r_dn = rci_at(dn, 8, 9)
    e = ema(np.array([1.0] * 9 + [2.0], dtype=float), 9)
    s = sma(np.arange(1.0, 6.0, dtype=float), 5)
    ok = (
        abs(r_up - 100.0) < 1e-9
        and abs(r_dn + 100.0) < 1e-9
        and np.isfinite(e[8])
        and abs(float(e[8]) - 1.0) < 1e-12
        and float(e[9]) > 1.0
        and abs(float(s[4]) - 3.0) < 1e-12
        and feature_usable_index(10) == 11
        and same_clock_baseline(np.arange(1.0, 11.0), 10) == 5.5
    )
    return {"ok": bool(ok), "rci_up": float(r_up), "rci_dn": float(r_dn)}


assert self_check()["ok"] is True
assert library_spec()["strategy_search"] is False
assert library_spec()["ready"] is True

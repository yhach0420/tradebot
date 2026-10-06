"""Causal 1-minute indicators. Completed bars only. No future. No extra families (MACD/ADX/RSI/ML)."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family import BB_PERIOD, BB_SIGMA, EMA_LONG, EMA_SHORT, RCI_PERIOD


def ema(close: np.ndarray, period: int) -> np.ndarray:
    x = np.asarray(close, dtype=float)
    n = int(x.size)
    out = np.full(n, np.nan, dtype=float)
    p = int(period)
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


def rolling_mean(x: np.ndarray, period: int) -> np.ndarray:
    v = np.asarray(x, dtype=float)
    n = int(v.size)
    out = np.full(n, np.nan, dtype=float)
    p = int(period)
    if n < p or p <= 0:
        return out
    c = np.cumsum(v)
    out[p - 1] = c[p - 1] / float(p)
    for i in range(p, n):
        out[i] = (c[i] - c[i - p]) / float(p)
    return out


def rolling_std_pop(x: np.ndarray, period: int) -> np.ndarray:
    v = np.asarray(x, dtype=float)
    n = int(v.size)
    out = np.full(n, np.nan, dtype=float)
    p = int(period)
    if n < p or p <= 0:
        return out
    for i in range(p - 1, n):
        w = v[i + 1 - p : i + 1]
        if not np.all(np.isfinite(w)):
            continue
        out[i] = float(np.std(w, ddof=0))
    return out


def bollinger(close: np.ndarray, period: int = BB_PERIOD, sigma: float = BB_SIGMA) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = rolling_mean(close, int(period))
    sd = rolling_std_pop(close, int(period))
    up = mid + float(sigma) * sd
    lo = mid - float(sigma) * sd
    return mid, up, lo


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


def rci_at(close: np.ndarray, end: int, period: int = RCI_PERIOD) -> float:
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


def rci_series(close: np.ndarray, period: int = RCI_PERIOD) -> np.ndarray:
    n = int(np.asarray(close).size)
    out = np.full(n, np.nan, dtype=float)
    for i in range(int(period) - 1, n):
        out[i] = rci_at(close, i, period=int(period))
    return out


def self_check() -> dict[str, Any]:
    up = np.arange(1.0, 10.0, dtype=float)
    dn = np.arange(9.0, 0.0, -1.0, dtype=float)
    r_up = rci_at(up, 8, 9)
    r_dn = rci_at(dn, 8, 9)
    ties = np.array([1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0], dtype=float)
    r_tie = rci_at(ties, 8, 9)
    e = ema(np.array([1.0] * 9 + [2.0], dtype=float), 9)
    ok = (
        abs(r_up - 100.0) < 1e-9
        and abs(r_dn + 100.0) < 1e-9
        and np.isfinite(r_tie)
        and abs(r_tie) <= 100.0 + 1e-9
        and np.isfinite(e[8])
        and abs(float(e[8]) - 1.0) < 1e-12
        and np.isfinite(e[9])
        and float(e[9]) > 1.0
    )
    return {
        "ok": bool(ok),
        "rci_up": float(r_up),
        "rci_dn": float(r_dn),
        "rci_tie": float(r_tie) if r_tie == r_tie else None,
        "ema_short": int(EMA_SHORT),
        "ema_long": int(EMA_LONG),
        "bb_period": int(BB_PERIOD),
        "rci_period": int(RCI_PERIOD),
    }

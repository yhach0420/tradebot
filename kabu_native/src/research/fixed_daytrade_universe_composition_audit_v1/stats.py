"""Contemporaneous stats only. No future-return ranking."""
from __future__ import annotations

import math
from typing import Sequence


def mean(xs: Sequence[float]) -> float | None:
    vals = [float(x) for x in xs]
    if not vals:
        return None
    return sum(vals) / len(vals)


def median(xs: Sequence[float]) -> float | None:
    vals = sorted(float(x) for x in xs)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def percentile(xs: Sequence[float], q: float) -> float | None:
    vals = sorted(float(x) for x in xs)
    if not vals:
        return None
    if q <= 0:
        return vals[0]
    if q >= 1:
        return vals[-1]
    pos = q * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1.0 - w) + vals[hi] * w


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 8:
        return None
    x = [float(xs[i]) for i in range(n)]
    y = [float(ys[i]) for i in range(n)]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy)


def ols_alpha_beta(y: Sequence[float], x: Sequence[float]) -> dict[str, float | None]:
    n = min(len(x), len(y))
    if n < 8:
        return {"n": n, "alpha": None, "beta": None, "r": None}
    xv = [float(x[i]) for i in range(n)]
    yv = [float(y[i]) for i in range(n)]
    mx = sum(xv) / n
    my = sum(yv) / n
    varx = sum((a - mx) ** 2 for a in xv)
    if varx == 0.0:
        return {"n": n, "alpha": None, "beta": None, "r": pearson(xv, yv)}
    cov = sum((a - mx) * (b - my) for a, b in zip(xv, yv))
    beta = cov / varx
    alpha = my - beta * mx
    return {"n": n, "alpha": alpha, "beta": beta, "r": pearson(xv, yv)}


assert abs((pearson([1, 2, 3, 4, 5, 6, 7, 8], [1, 2, 3, 4, 5, 6, 7, 8]) or 0) - 1.0) < 1e-12
assert median([1, 3, 2]) == 2

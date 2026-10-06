"""Frozen-candidate diagnostics. No new features. No threshold search."""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1 import TERCILE_N


def finite(xs: list[Any]) -> list[float]:
    out: list[float] = []
    for v in xs:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x):
            out.append(x)
    return out


def mean(xs: list[Any]) -> Optional[float]:
    ys = finite(xs)
    return statistics.mean(ys) if ys else None


def median(xs: list[Any]) -> Optional[float]:
    ys = finite(xs)
    return statistics.median(ys) if ys else None


def _rank_avg(xs: list[float]) -> list[float]:
    n = len(xs)
    order = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    sx = statistics.pstdev(xs)
    sy = statistics.pstdev(ys)
    if sx == 0 or sy == 0:
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    acc = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / float(len(xs))
    return acc / (sx * sy)


def spearman(xs: list[Any], ys: list[Any]) -> Optional[float]:
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys) if a is not None and b is not None]
    pairs = [(a, b) for a, b in pairs if math.isfinite(a) and math.isfinite(b)]
    if len(pairs) < 3:
        return None
    rx = _rank_avg([p[0] for p in pairs])
    ry = _rank_avg([p[1] for p in pairs])
    return pearson(rx, ry)


def trimmed_mean(xs: list[Any], p: float = 0.10) -> Optional[float]:
    ys = sorted(finite(xs))
    if not ys:
        return None
    k = int(len(ys) * float(p))
    core = ys[k : len(ys) - k] if k > 0 else ys
    return statistics.mean(core) if core else None


def pct(xs: list[Any], q: float) -> Optional[float]:
    ys = sorted(finite(xs))
    if not ys:
        return None
    if q <= 0:
        return ys[0]
    if q >= 1:
        return ys[-1]
    pos = (len(ys) - 1) * float(q)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    w = pos - lo
    return ys[lo] * (1.0 - w) + ys[hi] * w


def dist(xs: list[Any]) -> dict[str, Any]:
    ys = finite(xs)
    pos = [v for v in ys if v > 0]
    neg = [v for v in ys if v < 0]
    n = len(ys)
    return {
        "n": n,
        "mean": statistics.mean(ys) if ys else None,
        "median": statistics.median(ys) if ys else None,
        "trimmed_mean_10": trimmed_mean(ys, 0.10),
        "p25": pct(ys, 0.25),
        "p75": pct(ys, 0.75),
        "min": min(ys) if ys else None,
        "max": max(ys) if ys else None,
        "positive_n": len(pos),
        "negative_n": len(neg),
        "win_rate": (len(pos) / n) if n else None,
    }


def pos_neg(xs: list[Any]) -> tuple[int, int]:
    ys = finite(xs)
    return sum(1 for v in ys if v > 0), sum(1 for v in ys if v < 0)


def tercile_three(rows: list[dict[str, Any]], *, key: str = "value", n: int = TERCILE_N) -> Optional[dict[str, list[dict[str, Any]]]]:
    ranked = [r for r in rows if r.get(key) is not None]
    ranked = [r for r in ranked if math.isfinite(float(r[key]))]
    ranked.sort(key=lambda r: (-float(r[key]), str(r.get("symbol") or "")))
    k = int(n)
    if len(ranked) < 3 * k:
        return None
    return {
        "TOP": ranked[:k],
        "MIDDLE": ranked[k : 2 * k],
        "BOTTOM": ranked[-k:],
    }


def monotonic_mid(bottom: Optional[float], middle: Optional[float], top: Optional[float]) -> Optional[str]:
    if bottom is None or middle is None or top is None:
        return None
    if bottom < middle < top:
        return "BOTTOM < MIDDLE < TOP"
    if top > middle > bottom:
        return "TOP > MIDDLE > BOTTOM"
    if top > bottom:
        return "TOP > BOTTOM_NOT_MONOTONE"
    if top < bottom:
        return "INVERTED"
    return "FLAT"

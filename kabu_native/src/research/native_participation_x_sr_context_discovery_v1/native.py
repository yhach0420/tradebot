"""Causal clock-percentile TradingValue/Volume and frozen price displacement. No grid."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_participation_x_sr_context_discovery_v1 import (
    CLOSE_LOC_BEAR,
    CLOSE_LOC_BULL,
    DISP_LOOKBACK_BARS,
    RANGE_MEDIAN_BARS,
    TV_CLOCK_LOOKBACK_DAYS,
    TV_CLOCK_MIN_OBS,
    TV_EXPAND_PCTL,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


class ClockHistory:
    """Same minute-of-session, prior completed days only. No current-day future bars. No full-sample norm."""

    def __init__(self) -> None:
        self.tv: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self.vol: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    def pctl(self, store: dict[str, dict[str, list[float]]], symbol: str, minute: str, value: float) -> float | None:
        xs = [float(x) for x in store[str(symbol)][str(minute)] if _finite(x)]
        if len(xs) < int(TV_CLOCK_MIN_OBS) or not _finite(value):
            return None
        n = len(xs)
        k = int(sum(1 for x in xs if x <= float(value)))
        return float(k / n)

    def tv_pctl(self, symbol: str, minute: str, value: float) -> float | None:
        return self.pctl(self.tv, symbol, minute, value)

    def vol_pctl(self, symbol: str, minute: str, value: float) -> float | None:
        return self.pctl(self.vol, symbol, minute, value)

    def commit_day(self, symbol: str, rec: dict[str, Any], session_idx: list[int]) -> None:
        for i in session_idx:
            t = rec["t"][i]
            tv = rec["va"][i]
            v = rec["v"][i]
            if _finite(tv):
                xs = self.tv[str(symbol)][str(t)]
                xs.append(float(tv))
                if len(xs) > int(TV_CLOCK_LOOKBACK_DAYS):
                    del xs[0]
            if _finite(v):
                ys = self.vol[str(symbol)][str(t)]
                ys.append(float(v))
                if len(ys) > int(TV_CLOCK_LOOKBACK_DAYS):
                    del ys[0]


def close_location(h: float, l: float, c: float) -> float | None:
    if not (_finite(h) and _finite(l) and _finite(c)):
        return None
    if float(h) <= float(l):
        return None
    return float((float(c) - float(l)) / (float(h) - float(l)))


def displacement(rec: dict[str, Any], i: int, session_idx: list[int], pos: int | None = None) -> str | None:
    """Bullish/bearish on completed bar i. Prior 5 highs/lows and prior 20 ranges are completed bars only."""
    p = int(pos) if pos is not None else -1
    if p < max(int(DISP_LOOKBACK_BARS), int(RANGE_MEDIAN_BARS)):
        return None
    prev5 = session_idx[p - int(DISP_LOOKBACK_BARS) : p]
    prev20 = session_idx[p - int(RANGE_MEDIAN_BARS) : p]
    h, l, c = float(rec["h"][i]), float(rec["l"][i]), float(rec["c"][i])
    if not (_finite(h) and _finite(l) and _finite(c)):
        return None
    loc = close_location(h, l, c)
    if loc is None:
        return None
    rng = h - l
    med = float(np.median([float(rec["h"][j]) - float(rec["l"][j]) for j in prev20]))
    if not _finite(med) or rng < med:
        return None
    mx = max(float(rec["h"][j]) for j in prev5)
    mn = min(float(rec["l"][j]) for j in prev5)
    if c > mx and loc >= float(CLOSE_LOC_BULL):
        return "BULLISH"
    if c < mn and loc <= float(CLOSE_LOC_BEAR):
        return "BEARISH"
    return None


def participation_expand(pctl: float | None) -> bool:
    return pctl is not None and float(pctl) >= float(TV_EXPAND_PCTL)

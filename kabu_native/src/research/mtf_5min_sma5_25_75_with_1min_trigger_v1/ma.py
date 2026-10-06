"""Live-causal and confirmed 5-minute SMA helpers. Fixed 5/25/75. No EMA. No period search."""
from __future__ import annotations

from typing import Any

from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import ZONE_ATR_MULT
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import (
    aligned_reclaim,
    bar_touches,
    side_aligned,
    stack_aligned,
    through_level,
)

_ = (aligned_reclaim, bar_touches, side_aligned, stack_aligned, through_level)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def zone_half(atr1m: Any) -> float:
    if not _finite(atr1m) or float(atr1m) <= 0:
        return float("nan")
    return float(ZONE_ATR_MULT) * float(atr1m)


def in_sma25_zone(price: Any, sma25: Any, half: Any) -> bool:
    if not (_finite(price) and _finite(sma25) and _finite(half) and float(half) >= 0):
        return False
    return abs(float(price) - float(sma25)) <= float(half)


def bar_intersects_zone(high: Any, low: Any, sma25: Any, half: Any) -> bool:
    if not (_finite(high) and _finite(low) and _finite(sma25) and _finite(half) and float(half) >= 0):
        return False
    lo, hi = float(low), float(high)
    if lo > hi:
        lo, hi = hi, lo
    zlo, zhi = float(sma25) - float(half), float(sma25) + float(half)
    return not (hi < zlo or lo > zhi)


def reached_sma25_zone(close: Any, high: Any, low: Any, sma25: Any, atr1m: Any) -> bool:
    """Causal reach of the frozen ±0.10×1m ATR20 band around live 5m SMA25. No exact touch required."""
    half = zone_half(atr1m)
    if in_sma25_zone(close, sma25, half):
        return True
    return bar_intersects_zone(high, low, sma25, half)


def slope_dir(curr: Any, prev: Any, sign: int) -> float:
    if not (_finite(curr) and _finite(prev)):
        return float("nan")
    return float(sign) * (float(curr) - float(prev))

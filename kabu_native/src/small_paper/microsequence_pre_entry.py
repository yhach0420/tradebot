"""Pre-entry microsequence features from price ring (live-computable, no post-entry data)."""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

PRE_WINDOW_SEC = 120.0
SLOPE_WINDOW_SEC = 300.0


def _return_pct(start_px: float, end_px: float) -> Optional[float]:
    if start_px <= 0 or end_px <= 0:
        return None
    return round((end_px - start_px) / start_px * 100.0, 4)


def _window_prices(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    window_sec: float,
) -> list[tuple[float, float]]:
    return [(t, px) for t, px in price_ring if entry_ts - window_sec <= t <= entry_ts and px > 0]


def microsequence_ok_from_ring(price_ring: Sequence[tuple[float, float]], *, entry_ts: float) -> bool:
    return len(_window_prices(price_ring, entry_ts=entry_ts, window_sec=PRE_WINDOW_SEC)) >= 3


def bounce_from_recent_low_ring(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    window_sec: float = PRE_WINDOW_SEC,
) -> Optional[float]:
    pts = _window_prices(price_ring, entry_ts=entry_ts, window_sec=window_sec)
    if len(pts) < 2 or entry_px <= 0:
        return None
    recent_low = min(px for _, px in pts)
    return _return_pct(recent_low, entry_px)


def fall_from_recent_high_ring(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    window_sec: float = PRE_WINDOW_SEC,
) -> Optional[float]:
    pts = _window_prices(price_ring, entry_ts=entry_ts, window_sec=window_sec)
    if len(pts) < 2 or entry_px <= 0:
        return None
    recent_high = max(px for _, px in pts)
    ret = _return_pct(entry_px, recent_high)
    return round(-ret, 4) if ret is not None else None


def slope_5min_pct_per_min_ring(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    window_sec: float = SLOPE_WINDOW_SEC,
) -> Optional[float]:
    pts = _window_prices(price_ring, entry_ts=entry_ts, window_sec=window_sec)
    if len(pts) < 3:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 60.0 for t, _ in pts]
    ys = [px for _, px in pts]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    if mean_y <= 0:
        return None
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = num / den
    return round(slope / mean_y * 100.0, 4)


def compute_microsequence_pre_entry_features(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: Optional[float],
    entry_px: float,
) -> dict[str, Any]:
    if entry_ts is None or entry_px <= 0:
        return {
            "microsequence_pre_entry_ok": False,
            "bounce_from_recent_low": None,
            "fall_from_recent_high": None,
            "slope_5min": None,
        }
    ring = list(price_ring)
    return {
        "microsequence_pre_entry_ok": microsequence_ok_from_ring(ring, entry_ts=entry_ts),
        "bounce_from_recent_low": bounce_from_recent_low_ring(ring, entry_ts=entry_ts, entry_px=entry_px),
        "fall_from_recent_high": fall_from_recent_high_ring(ring, entry_ts=entry_ts, entry_px=entry_px),
        "slope_5min": slope_5min_pct_per_min_ring(ring, entry_ts=entry_ts),
    }

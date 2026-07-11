"""
Pre-entry MFE proxy — live-computable only (no post-entry / exit data).

Uses price ticks with t <= entry_ts within a pre-entry window.
Forbidden: exit price, hold MFE, pnl, future ticks after accept.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

DEFAULT_WINDOW_SEC = 120.0
SOURCE_PRICE_RING = "price_ring_pre_entry"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_mfe_pre_entry_pct(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: float,
    entry_px: float,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> Optional[float]:
    """
    Max favorable excursion observable before/at entry (% of entry price).

    Long-side: max(0, window_high - entry_px) / entry_px * 100 using ticks with
    entry_ts - window_sec <= t <= entry_ts only.
    """
    if entry_px <= 0 or entry_ts <= 0:
        return None
    window = [(t, px) for t, px in price_ring if entry_ts - window_sec <= t <= entry_ts and px > 0]
    if len(window) < 2:
        return None
    max_px = max(px for _, px in window)
    headroom_pct = max(0.0, (max_px - entry_px) / entry_px * 100.0)
    return round(headroom_pct, 4)


def compute_mfe_pre_entry_fields(
    price_ring: Sequence[tuple[float, float]],
    *,
    entry_ts: Optional[float],
    entry_px: float,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> dict[str, Any]:
    if entry_ts is None:
        return {
            "mfe_pre_entry_pct": None,
            "mfe_pre_entry_source": None,
            "mfe_pre_entry_window_sec": window_sec,
        }
    pct = compute_mfe_pre_entry_pct(
        price_ring, entry_ts=entry_ts, entry_px=entry_px, window_sec=window_sec
    )
    return {
        "mfe_pre_entry_pct": pct,
        "mfe_pre_entry_source": SOURCE_PRICE_RING if pct is not None else None,
        "mfe_pre_entry_window_sec": window_sec,
    }


def mfe_pre_entry_from_price_series(
    series: Sequence[tuple[Any, float]],
    *,
    entry_ts: float,
    entry_px: float,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> Optional[float]:
    """Research helper: datetime/float series -> ring tuples."""
    from datetime import datetime

    ring: list[tuple[float, float]] = []
    for ts, px in series:
        if px <= 0:
            continue
        if isinstance(ts, datetime):
            t = ts.timestamp()
        else:
            t = float(ts)
        ring.append((t, float(px)))
    return compute_mfe_pre_entry_pct(ring, entry_ts=entry_ts, entry_px=entry_px, window_sec=window_sec)


def leaky_actual_mfe_pct(trade: Mapping[str, Any]) -> Optional[float]:
    """Audit-only: post-entry peak MFE (FORBIDDEN for runtime gate)."""
    return _float(trade.get("peak_mfe_pct")) or _float(trade.get("mfe_pct")) or _float(trade.get("rolling_mfe_pct"))

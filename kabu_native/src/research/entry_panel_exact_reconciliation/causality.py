"""Causal cutoff helpers. Reuse Exact feature functions. No second feature catalog."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def max_source_event_time(
    board: dict[str, Any],
    t0: float,
    *,
    series: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    """Max event timestamp actually consumed at decision_anchor t0.

    last board: searchsorted(t, t0) last t<=t0
    180s lookback: mask t<=t0
    volume/VWAP series: searchsorted(t, t0) last t<=t0 even if cache holds later rows
    """
    used: list[float] = []
    last_board_t = None
    lookback_t_max = None
    series_t_used = None
    series_has_future_unused = False
    t = board.get("t") if board else None
    if t is not None and getattr(t, "size", 0):
        i = int(np.searchsorted(t, float(t0), side="right") - 1)
        if i >= 0:
            last_board_t = float(t[i])
            used.append(last_board_t)
            mask = (t <= float(t0) + 1e-12) & (t >= float(t0) - 180.0 - 1e-12)
            if bool(np.any(mask)):
                lookback_t_max = float(np.max(t[mask]))
                used.append(lookback_t_max)
    if series is not None:
        st = series.get("t")
        if st is not None and getattr(st, "size", 0):
            if bool(np.any(st > float(t0) + 1e-12)):
                series_has_future_unused = True
            j = int(np.searchsorted(st, float(t0), side="right") - 1)
            if j >= 0:
                series_t_used = float(st[j])
                used.append(series_t_used)
    mx = max(used) if used else None
    future = bool(mx is not None and mx > float(t0) + 1e-9)
    return {
        "last_board_t": last_board_t,
        "lookback_t_max": lookback_t_max,
        "series_t_used": series_t_used,
        "series_has_future_unused": series_has_future_unused,
        "max_source_event_time": mx,
        "future_event_use": future,
    }


def code_causality_proof() -> dict[str, str]:
    return {
        "CLOCK_FIRE": (
            "V1RNativeEntryLive.maybe_fire_anchor waits until now_t > t0, then "
            "_run_anchor snapshots last board via np.searchsorted(board['t'], t0, side='right')-1."
        ),
        "CURRENT_FEATURES": (
            "preentry_from_board uses searchsorted(t, signal_t) and lookback masks t<=signal_t."
        ),
        "C3_LOOKBACK": (
            "extra_from_board drawdown/range masks (t<=t0)&(t>=t0-180). No future rows."
        ),
        "C3_VWAP": (
            "volume_features_at: i=searchsorted(series['t'], t0, side='right')-1. "
            "Uses series t[i]<=t0 even if C3LookupEngine._series first-CLOCK cache "
            "still holds a later unused tail."
        ),
        "C3_CACHE": (
            "C3LookupEngine._series / CanonicalEngine._c3_series cache source_series "
            "at first CLOCK touch. Cutoff is applied at feature time, not by dropping the cache."
        ),
        "EXACT_EXEC": (
            "classify_t0_row reads ingest-time executable flag from the last t<=t0 src row. "
            "No fill_time / nearest-anchor / exit_time origin."
        ),
    }

"""Path helpers with pre-entry lookback for noise-band range (no imputation)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from research.eec_noise_hysteresis.constants import PATH_MAX_SEC
from research.price_flow_exit.path_mfe import PathBar, bars_after_entry
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick

LOOKBACK_SEC = 45.0


def path_with_lookback(
    ticks: Sequence[PushTick],
    entry_time: datetime,
    *,
    lookback_sec: float = LOOKBACK_SEC,
    max_sec: float = PATH_MAX_SEC,
) -> tuple[list[PathBar], int]:
    """Return path starting lookback_sec before entry, and index of first bar at/after entry."""
    bars = aggregate_to_seconds(list(ticks)) if ticks else []
    t0 = entry_time - timedelta(seconds=lookback_sec)
    path = bars_after_entry(bars, t0, max_sec=max_sec + lookback_sec)
    entry_i = 0
    for i, b in enumerate(path):
        if b.t >= entry_time:
            entry_i = i
            break
    else:
        entry_i = len(path)
    return path, entry_i

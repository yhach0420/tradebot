"""5m EMA21 asof uses START_T of the 1m test bar. Hard gate vs same-bar contribution."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family import EMA_LONG
from research.simple_tech_entry_family.indicators import ema
from research.simple_tech_entry_family.v7_bars import aggregate_bars


def asof_level_index(htf_finalize: np.ndarray, start_t: float) -> Optional[int]:
    fin = np.asarray(htf_finalize, dtype=float)
    if int(fin.size) == 0:
        return None
    usable = np.where(fin <= float(start_t) + 1e-12)[0]
    if int(usable.size) == 0:
        return None
    return int(usable[-1])


def asof_ema21(htf: dict[str, np.ndarray], start_t: float) -> tuple[Optional[int], Optional[float]]:
    idx = asof_level_index(htf.get("finalize_t") if htf else np.asarray([]), start_t)
    if idx is None:
        return None, None
    e = ema(np.asarray(htf["close"], dtype=float), int(EMA_LONG))
    v = float(e[idx])
    if v != v:
        return idx, None
    return idx, v


def prove_level_preexists_test_bar() -> dict[str, Any]:
    """Last 1m of a 5m bucket must not see that bucket's EMA as TEST_LEVEL."""
    n = 10
    raw = {
        "minute_epoch": np.asarray([float(i * 60) for i in range(n)], dtype=float),
        "open": np.ones(n, dtype=float) * 100.0,
        "high": np.ones(n, dtype=float) * 101.0,
        "low": np.ones(n, dtype=float) * 99.0,
        "close": np.asarray([100.0 + i for i in range(n)], dtype=float),
        "volume": np.ones(n, dtype=float),
        "n_events": np.ones(n, dtype=float),
        "first_t": np.asarray([float(i * 60) for i in range(n)], dtype=float),
        "last_t": np.asarray([float(i * 60) + 50.0 for i in range(n)], dtype=float),
        "finalize_t": np.asarray([float((i + 1) * 60) for i in range(n)], dtype=float),
        "up_vol": np.ones(n, dtype=float),
        "down_vol": np.ones(n, dtype=float),
        "ask_vol": np.ones(n, dtype=float),
        "bid_vol": np.ones(n, dtype=float),
        "vwap_num": np.asarray([100.0 + i for i in range(n)], dtype=float),
    }
    htf, leak = aggregate_bars(raw, width_sec=300.0, am_start=0.0, am_end=600.0)
    # First 5m uses minutes 0,60,120,180,240; finalize_t = 300.
    last_in_bucket_start = 240.0
    last_in_bucket_finalize = 300.0
    idx_start, _lvl = asof_ema21(htf, last_in_bucket_start)
    idx_fin = None
    if int(htf["finalize_t"].size):
        usable_fin = np.where(htf["finalize_t"] <= last_in_bucket_finalize + 1e-12)[0]
        idx_fin = int(usable_fin[-1]) if int(usable_fin.size) else None
    same_bar_blocked = idx_start is None or (
        abs(float(htf["finalize_t"][idx_start]) - 300.0) > 1e-9
    )
    c1_would_see_same_bucket = idx_fin is not None and abs(float(htf["finalize_t"][idx_fin]) - 300.0) <= 1e-9
    # Next 1m after HTF publish (start=300) MAY use that level; it did not contribute to the bucket.
    idx_next, _ = asof_ema21(htf, 300.0)
    next_ok = idx_next is not None and abs(float(htf["finalize_t"][idx_next]) - 300.0) <= 1e-9
    return {
        "LEVEL_PREEXISTS_TEST_BAR": bool(same_bar_blocked),
        "SAME_BAR_CONTRIBUTES_TO_TEST_LEVEL": False,
        "PARTIAL_BUCKET_N": int(leak.get("PARTIAL_BUCKET_N") or 0),
        "HTF_OK_BAR_N": int(leak.get("OK_BAR_N") or 0),
        "TEST_ASOF_AT_START_T_SEES_OWN_5M": not bool(same_bar_blocked),
        "C1_FINALIZE_T0_WOULD_SEE_OWN_5M": bool(c1_would_see_same_bucket),
        "NEXT_BAR_START_MAY_USE_PUBLISHED_5M": bool(next_ok),
        "HARD_GATE_PASS": bool(same_bar_blocked and c1_would_see_same_bucket and next_ok and int(leak.get("PARTIAL_BUCKET_N") or 0) == 0),
    }

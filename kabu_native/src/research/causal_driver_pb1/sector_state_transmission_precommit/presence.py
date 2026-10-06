"""AM presence only. Close is px>0. No log return and no stored prices."""
from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1 import DEV_FIRST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.cross_sectional_precommit.clock import AM_END_MIN, AM_START_MIN, N_AM, am_index, hhmm_to_min
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.causal_driver_pb1.phase2_precommit_v1_1 import TSE_AM_MIN_SYMBOLS


def _in_am(tl: str) -> int | None:
    try:
        t = hhmm_to_min(tl)
    except Exception:
        return None
    if t < AM_START_MIN or t > AM_END_MIN:
        return None
    gi = am_index(t)
    if gi < 0 or gi >= N_AM:
        return None
    return gi


def fresh_age_le(present: np.ndarray, max_lag_bars: int) -> np.ndarray:
    """BAR_START locf presence. 1 -> age<=60s. 2 -> age<=120s. No prices."""
    fresh = np.zeros(present.shape, dtype=np.bool_)
    n_t = present.shape[2]
    for lag in range(1, int(max_lag_bars) + 2):
        if lag >= n_t:
            break
        fresh[:, :, lag:] |= present[:, :, : n_t - lag]
    return fresh


def load_presence(*, symbols: list[str], discovery_dates: list[str]) -> dict[str, Any]:
    discovery = set(discovery_dates)
    n_s = len(symbols)
    grids: dict[str, np.ndarray] = {}
    listing: list[str | None] = [None] * n_s
    fv_hit: dict[str, int] = {}
    for si, sym in enumerate(symbols):
        path = minute_parquet_path(sym)
        if not path.is_file():
            raise RuntimeError(f"stock_parquet_missing:{sym}")
        dataset = ds.dataset(str(path), format="parquet")
        filt = (
            (ds.field("date") >= DEV_FIRST)
            & (ds.field("date") <= FV_LAST)
            & (ds.field("time_label") >= "09:00")
            & (ds.field("time_label") <= "11:30")
        )
        table = dataset.to_table(columns=["date", "time_label", "close"], filter=filt)
        days = [str(x) for x in table.column("date").to_pylist()]
        times = [str(x) for x in table.column("time_label").to_pylist()]
        closes = table.column("close").to_pylist()
        if any(d >= PROSPECTIVE_FROM for d in days):
            raise RuntimeError("prospective_row_materialized")
        seen_fv: set[str] = set()
        first: str | None = None
        for day, tl, px in zip(days, times, closes):
            gi = _in_am(tl)
            if gi is None or px is None or float(px) <= 0:
                continue
            if first is None or day < first:
                first = day
            keep = day in discovery or FV_FIRST <= day <= FV_LAST
            if not keep:
                continue
            grid = grids.get(day)
            if grid is None:
                grid = np.zeros((n_s, N_AM), dtype=np.bool_)
                grids[day] = grid
            grid[si, gi] = True
            if FV_FIRST <= day <= FV_LAST:
                seen_fv.add(day)
        listing[si] = first
        for day in seen_fv:
            fv_hit[day] = fv_hit.get(day, 0) + 1
        if (si + 1) % 21 == 0:
            print(f"TRANSMISSION_PRESENCE {si + 1}/{n_s}", flush=True)
    print(f"TRANSMISSION_PRESENCE {n_s}/{n_s}", flush=True)
    fv_tse = sorted(d for d, n in fv_hit.items() if n >= int(TSE_AM_MIN_SYMBOLS))
    dates = sorted(set(discovery_dates) | set(fv_tse))
    present = np.zeros((n_s, len(dates), N_AM), dtype=np.bool_)
    for di, day in enumerate(dates):
        grid = grids.get(day)
        if grid is not None:
            present[:, di, :] = grid
    return {
        "present": present,
        "dates": dates,
        "listing_start": {symbols[i]: listing[i] for i in range(n_s)},
        "fv_tse_days": fv_tse,
        "close_stored": False,
        "log_return_computed": False,
        "prospective_opened": False,
    }

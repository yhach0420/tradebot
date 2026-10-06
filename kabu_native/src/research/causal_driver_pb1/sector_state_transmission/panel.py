"""BAR_START price panels. Discovery cannot read FV. FV cannot read prospective."""
from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1 import FV_FIRST, PROSPECTIVE_FROM
from research.causal_driver_pb1.cross_sectional_discovery.features import build_am, listed_mask
from research.causal_driver_pb1.sector_breadth_discovery.features import listing_from_px
from research.causal_driver_pb1.phase2_discovery.clock import GRID_END_MIN, GRID_START_MIN, N_GRID, grid_index, hhmm_to_min
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path


def load_prices(*, symbols: list[str], dates: list[str], stage: str) -> dict[str, Any]:
    if stage not in {"DISCOVERY", "FV"}:
        raise RuntimeError("stage")
    date_index = {d: i for i, d in enumerate(dates)}
    if stage == "DISCOVERY" and any(d >= FV_FIRST for d in dates):
        raise RuntimeError("discovery_dates_enter_fv")
    if stage == "FV" and (any(d < FV_FIRST or d >= PROSPECTIVE_FROM for d in dates)):
        raise RuntimeError("fv_dates_outside_window")
    close = np.full((len(symbols), len(dates), N_GRID), np.nan, dtype=np.float64)
    lo = f"{GRID_START_MIN // 60:02d}:{GRID_START_MIN % 60:02d}"
    hi = f"{GRID_END_MIN // 60:02d}:{GRID_END_MIN % 60:02d}"
    date_lo = min(dates)
    date_hi = max(dates)
    for si, sym in enumerate(symbols):
        path = minute_parquet_path(sym)
        if not path.is_file():
            raise RuntimeError(f"stock_parquet_missing:{sym}")
        dataset = ds.dataset(str(path), format="parquet")
        filt = (
            (ds.field("date") >= date_lo)
            & (ds.field("date") <= date_hi)
            & (ds.field("time_label") >= lo)
            & (ds.field("time_label") <= hi)
        )
        table = dataset.to_table(columns=["date", "time_label", "close"], filter=filt)
        days = [str(x) for x in table.column("date").to_pylist()]
        times = [str(x) for x in table.column("time_label").to_pylist()]
        vals = table.column("close").to_pylist()
        if stage == "DISCOVERY" and any(d >= FV_FIRST for d in days):
            raise RuntimeError("fv_stock_row_materialized")
        if stage == "FV" and any(d >= PROSPECTIVE_FROM for d in days):
            raise RuntimeError("prospective_row_materialized")
        for day, tl, px in zip(days, times, vals):
            di = date_index.get(day)
            if di is None or px is None or float(px) <= 0:
                continue
            try:
                tmin = hhmm_to_min(tl)
            except Exception:
                continue
            if tmin < GRID_START_MIN or tmin > GRID_END_MIN:
                continue
            close[si, di, grid_index(tmin)] = float(px)
        if (si + 1) % 21 == 0:
            print(f"TRANSMISSION_PX {stage} {si + 1}/{len(symbols)}", flush=True)
    am = build_am(close=close)
    listing = listing_from_px(px=am["px"], symbols=symbols, dates=dates)
    listed = listed_mask(symbols=symbols, dates=dates, listing_start=listing)
    return {"px": am["px"], "age": am["age"], "listed": listed, "dates": list(dates), "symbols": list(symbols), "stage": stage}

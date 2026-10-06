"""Input panels. Stage A never reads C1 stock rows. No FV rows. No forward-fill."""
from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1 import C1_LAST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.contracts.enums import QualityStatus
from research.causal_driver_pb1.phase1 import SOURCE_ID
from research.causal_driver_pb1.phase1.bars import load_and_pair
from research.causal_driver_pb1.phase1.dates import yyyymmdd
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.phase2_discovery.clock import GRID_END_MIN, GRID_START_MIN, N_GRID, grid_index, hhmm_to_min

EPS = 1e-12


def load_fx_panel(*, dates: list[str], utc_first: str, utc_last: str, jst_lo: str, jst_hi: str) -> dict[str, Any]:
    packed = load_and_pair(utc_first=utc_first, utc_last=utc_last, source_id=SOURCE_ID)
    date_index = {d: i for i, d in enumerate(dates)}
    n_d = len(dates)
    mid = np.full((n_d, N_GRID), np.nan, dtype=np.float64)
    valid = np.zeros((n_d, N_GRID), dtype=np.bool_)
    dropped_out_of_stage = 0
    for bar in packed.get("bars") or []:
        day = bar.jst_date
        if day < jst_lo or day > jst_hi:
            dropped_out_of_stage += 1
            continue
        di = date_index.get(day)
        if di is None:
            continue
        tmin = bar.bar_start.hour * 60 + bar.bar_start.minute
        if tmin < GRID_START_MIN or tmin > GRID_END_MIN:
            continue
        gi = grid_index(tmin)
        if bar.quality_status == QualityStatus.VALID and bar.mid_close and bar.mid_close > 0:
            mid[di, gi] = float(bar.mid_close)
            valid[di, gi] = True
    unexpected = ~valid
    return {
        "dates": list(dates),
        "mid": mid,
        "valid": valid,
        "unexpected_missing": unexpected,
        "dropped_out_of_stage_n": dropped_out_of_stage,
        "conflicting_duplicate_n": int(packed.get("conflicting_duplicate_n") or 0),
        "paired_bar_n": int(packed.get("paired_bar_n") or 0),
        "utc_first": utc_first,
        "utc_last": utc_last,
        "jst_lo": jst_lo,
        "jst_hi": jst_hi,
    }


def load_stock_panel(
    *,
    symbols: tuple[str, ...],
    dates: list[str],
    date_lo: str,
    date_hi: str,
    ledger: StageLedger,
    stage: str,
) -> dict[str, Any]:
    if date_hi >= FV_FIRST:
        raise RuntimeError("stock_panel_would_open_fv")
    if stage == "STAGE_A" and date_hi > DEV_LAST:
        raise RuntimeError("stage_a_date_hi_beyond_dev")
    if stage == "STAGE_B" and date_lo <= DEV_LAST:
        raise RuntimeError("stage_b_must_not_reload_dev_as_c1")
    date_index = {d: i for i, d in enumerate(dates)}
    n_s = len(symbols)
    n_d = len(dates)
    close = np.full((n_s, n_d, N_GRID), np.nan, dtype=np.float64)
    rows_kept = 0
    max_date = ""
    lo_m = GRID_START_MIN
    hi_m = GRID_END_MIN
    for si, sym in enumerate(symbols):
        path = minute_parquet_path(sym)
        if not path.is_file():
            raise RuntimeError(f"stock_parquet_missing:{sym}")
        dataset = ds.dataset(str(path), format="parquet")
        filt = (
            (ds.field("date") >= date_lo)
            & (ds.field("date") <= date_hi)
            & (ds.field("time_label") >= "08:00")
            & (ds.field("time_label") <= "11:30")
        )
        table = dataset.to_table(columns=["date", "time_label", "close"], filter=filt)
        dates_col = [str(x) for x in table.column("date").to_pylist()]
        times_col = [str(x) for x in table.column("time_label").to_pylist()]
        close_col = table.column("close").to_pylist()
        if dates_col and max(dates_col) > max_date:
            max_date = max(dates_col)
        if any(d >= FV_FIRST for d in dates_col):
            raise RuntimeError("fv_stock_row_materialized")
        if stage == "STAGE_A" and any(d > DEV_LAST for d in dates_col):
            raise RuntimeError("stage_a_read_c1_or_later_stock")
        if stage == "STAGE_B":
            n_c1 = sum(1 for d in dates_col if d > DEV_LAST)
            ledger.note_c1_stock_rows(n_c1)
        for day, tl, px in zip(dates_col, times_col, close_col):
            di = date_index.get(day)
            if di is None:
                continue
            try:
                tmin = hhmm_to_min(tl)
            except Exception:
                continue
            if tmin < lo_m or tmin > hi_m:
                continue
            if px is None:
                continue
            val = float(px)
            if val <= 0:
                continue
            close[si, di, grid_index(tmin)] = val
            rows_kept += 1
        if (si + 1) % 21 == 0:
            print(f"STOCK_PANEL {stage} {si + 1}/{n_s}", flush=True)
    if stage == "STAGE_A" and max_date and max_date > DEV_LAST:
        raise RuntimeError("stage_a_max_date_beyond_dev")
    if stage == "STAGE_B" and max_date and max_date > C1_LAST:
        raise RuntimeError("stage_b_max_date_beyond_c1")
    return {
        "symbols": list(symbols),
        "dates": list(dates),
        "close": close,
        "valid": np.isfinite(close) & (close > 0),
        "rows_kept": rows_kept,
        "max_date": max_date,
        "date_lo": date_lo,
        "date_hi": date_hi,
        "stage": stage,
    }

"""Prove equity 1m BAR_START semantics on Development dates only. No returns. No FV rows."""
from __future__ import annotations

from typing import Any

import pyarrow.dataset as ds

from research.aligned_historical_panel_v1.time_semantics import SEMANTICS_BAR_START, bar_clock
from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.contracts.time import bar_start_available_at, jst
from research.causal_driver_pb1.phase2_precommit import STOCK_PRICE_FIELD, STOCK_TIMESTAMP_SEMANTICS
from research.causal_driver_pb1.phase2_precommit.isolation import MINUTE_REF


def minute_parquet_path(symbol: str):
    return MINUTE_REF / f"minute_{symbol}_20240917_20260911.parquet"


def _table(path, *, columns: list[str], date_lo: str, date_hi: str, time_label: str | None = None):
    dataset = ds.dataset(str(path), format="parquet")
    filt = (ds.field("date") >= date_lo) & (ds.field("date") <= date_hi)
    if date_hi >= FV_FIRST:
        raise RuntimeError("stock_probe_would_open_frozen_validation")
    if time_label is not None:
        filt = filt & (ds.field("time_label") == time_label)
    return dataset.to_table(columns=columns, filter=filt)


def prove_stock_timestamp_semantics(*, probe_symbol: str = "7203") -> dict[str, Any]:
    path = minute_parquet_path(probe_symbol)
    if not path.is_file():
        return {
            "pass": False,
            "reason": "PROBE_PARQUET_MISSING",
            "path": str(path),
            "stock_timestamp_semantics_proven": False,
        }
    cols = ["date", "time_label", STOCK_PRICE_FIELD, "bar_start_jst", "bar_end_jst", "available_at_jst"]
    table = _table(path, columns=cols, date_lo=DEV_FIRST, date_hi=DEV_FIRST, time_label="09:00")
    if table.num_rows != 1:
        return {
            "pass": False,
            "reason": "DEV_0900_BAR_NOT_UNIQUE",
            "n": int(table.num_rows),
            "stock_timestamp_semantics_proven": False,
        }
    row = {k: table.column(k)[0].as_py() for k in table.column_names}
    clock = bar_clock(labeled_date=DEV_FIRST, labeled_time="09:00", semantics=SEMANTICS_BAR_START)
    expected_available = bar_start_available_at(jst(2024, 9, 17, 9, 0, 0))
    proven = (
        str(row.get("time_label")) == "09:00"
        and str(row.get("bar_start_jst")) == "2024-09-17T09:00:00+09:00"
        and str(row.get("available_at_jst")) == "2024-09-17T09:01:00+09:00"
        and clock is not None
        and clock["available_at_jst"].endswith("09:01:00+09:00")
        and expected_available.isoformat() == "2024-09-17T09:01:00+09:00"
        and row.get(STOCK_PRICE_FIELD) is not None
        and float(row[STOCK_PRICE_FIELD]) > 0
    )
    c1 = _table(path, columns=["date"], date_lo=C1_FIRST, date_hi=C1_LAST)
    c1_dates = sorted({str(x) for x in c1.column("date").to_pylist()})
    if any(d >= FV_FIRST for d in c1_dates):
        return {"pass": False, "reason": "FV_DATE_MATERIALIZED", "stock_timestamp_semantics_proven": False}
    schema_names = list(ds.dataset(str(path), format="parquet").schema.names)
    return {
        "pass": bool(proven),
        "stock_timestamp_semantics_proven": bool(proven),
        "reason": None if proven else "CLOCK_MISMATCH",
        "probe_symbol": probe_symbol,
        "probe_date": DEV_FIRST,
        "probe_time_label": "09:00",
        "bar_timestamp_convention": STOCK_TIMESTAMP_SEMANTICS,
        "available_at": "bar_start_plus_1_minute",
        "price_field": STOCK_PRICE_FIELD,
        "timezone": "Asia/Tokyo",
        "mid_fabricated": False,
        "bid_ask_in_equity_minute": False,
        "sample_close": float(row[STOCK_PRICE_FIELD]),
        "sample_bar_start_jst": row.get("bar_start_jst"),
        "sample_available_at_jst": row.get("available_at_jst"),
        "join_rule": "available_at_jst <= decision_time_jst",
        "response_start_price": "last_completed_close_with_available_at_le_T",
        "response_end_price": "last_completed_close_with_available_at_le_T_plus_h",
        "outcome_class": "INFORMATION_DISCOVERY_OUTCOME",
        "not_tradable_pnl": True,
        "not_fill_return": True,
        "parquet_schema_names": schema_names,
        "c1_date_n_probe_symbol": len(c1_dates),
        "c1_date_first": c1_dates[0] if c1_dates else None,
        "c1_date_last": c1_dates[-1] if c1_dates else None,
        "fv_rows_read": False,
        "filename_end_date_on_disk": "20260911",
        "row_filter_hard_stop": C1_LAST,
        "dev_last": DEV_LAST,
        "path": str(path).replace("\\", "/"),
    }

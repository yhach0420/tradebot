"""Historical Family D scan. Dates stop at 20260421."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pyarrow.dataset as ds

import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.context_conditioned_stock_setup import SECTOR_MAPPING_SHA256
from research.relative_strength_family_d import C1_LAST, DEV_FIRST, UNIVERSE_SHA256
from research.relative_strength_family_d_discovery_v1.evaluate import evaluate_day

FV_FIRST = "20260422"


def _clock(labels: list[str]) -> np.ndarray:
    return np.asarray([int(x[:2]) * 60 + int(x[3:5]) for x in labels], dtype=np.int32)


def scan() -> dict[str, Any]:
    universe = load_research_observation_universe()
    if universe.source_sha256 != UNIVERSE_SHA256:
        raise RuntimeError("universe_sha_mismatch")
    mapping = bind_sector_mapping()
    if mapping.get("sector_mapping_sha256") != SECTOR_MAPPING_SHA256 or not mapping.get("pass"):
        raise RuntimeError("sector_mapping_mismatch")
    symbols = list(universe.ordered_symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    sector_of: dict[int, str] = {}
    members: dict[str, list[int]] = defaultdict(list)
    for row in mapping["rows"]:
        sym = str(row["symbol"])
        sid = str(row.get("sector_id") or "")
        if sym in index and sid:
            sector_of[index[sym]] = sid
            members[sid].append(index[sym])
    peers_of: dict[int, np.ndarray] = {}
    for rows in members.values():
        arr = np.asarray(rows, dtype=np.int32)
        for sym_i in rows:
            peers_of[int(sym_i)] = arr[arr != int(sym_i)]
    day_bars: dict[str, list] = defaultdict(list)
    dates: set[str] = set()
    max_date = ""
    rows_read = 0
    for nth, sym in enumerate(symbols, start=1):
        path = minute_parquet_path(sym)
        if not path.is_file():
            raise RuntimeError(f"missing_minute_parquet:{sym}")
        table = ds.dataset(str(path), format="parquet").to_table(
            columns=["date", "time_label", "high", "low", "close", "volume"],
            filter=(ds.field("date") >= DEV_FIRST) & (ds.field("date") <= C1_LAST),
        )
        frame = table.to_pandas()
        if frame.empty:
            continue
        frame["date"] = frame["date"].astype(str)
        if bool(frame["date"].ge(FV_FIRST).any()) or bool(frame["date"].gt(C1_LAST).any()):
            raise RuntimeError("frozen_validation_rows_loaded")
        rows_read += int(len(frame))
        max_date = max(max_date, str(frame["date"].max()))
        sym_i = index[sym]
        for day, group in frame.groupby("date", sort=False):
            day = str(day)
            if day < DEV_FIRST or day > C1_LAST:
                raise RuntimeError("date_outside_seal")
            dates.add(day)
            group = group.sort_values("time_label")
            labels = [str(x)[:5] for x in group["time_label"].tolist()]
            day_bars[day].append(
                (
                    sym_i,
                    _clock(labels),
                    group["high"].to_numpy(dtype=float),
                    group["low"].to_numpy(dtype=float),
                    group["close"].to_numpy(dtype=float),
                    group["volume"].to_numpy(dtype=float),
                )
            )
        if nth % 15 == 0 or nth == len(symbols):
            print(f"LOAD {nth}/{len(symbols)} rows={rows_read}", flush=True)
    if not max_date or max_date > C1_LAST or max_date >= FV_FIRST:
        raise RuntimeError("max_date_crossed_firewall")
    events: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    for nth, day in enumerate(sorted(day_bars), start=1):
        one, counts = evaluate_day(day, len(symbols), day_bars[day], peers_of, sector_of)
        events.extend(one)
        for key, value in counts.items():
            totals[key] = totals.get(key, 0.0) + float(value)
        if nth % 40 == 0 or nth == len(day_bars):
            print(f"DAY {nth}/{len(day_bars)} events={len(events)}", flush=True)
    symbol_name = np.asarray(symbols, dtype=object)
    packed = {
        "date": np.asarray([row["date"] for row in events], dtype=object),
        "symbol": np.asarray([symbol_name[int(row["symbol"])] for row in events], dtype=object),
        "sector": np.asarray([row["sector"] for row in events], dtype=object),
        "population": np.asarray([row["population"] for row in events], dtype=object),
        "market_breadth": np.asarray([row["market_breadth"] for row in events], dtype=float),
        "sector_gap": np.asarray([row["sector_gap"] for row in events], dtype=float),
        "relative_strength": np.asarray([row["relative_strength"] for row in events], dtype=float),
        "volume_ratio": np.asarray([row["volume_ratio"] for row in events], dtype=float),
        "break_gap": np.asarray([row["break_gap"] for row in events], dtype=float),
        "ret3": np.asarray([row["ret3"] for row in events], dtype=float),
        "ret5": np.asarray([row["ret5"] for row in events], dtype=float),
        "ret10": np.asarray([row["ret10"] for row in events], dtype=float),
        "rel5": np.asarray([row["rel5"] for row in events], dtype=float),
        "mfe5": np.asarray([row["mfe5"] for row in events], dtype=float),
        "mae5": np.asarray([row["mae5"] for row in events], dtype=float),
    }
    return {
        "events": packed,
        "counts": totals,
        "dates": sorted(dates),
        "symbols": symbols,
        "universe_sha256": universe.source_sha256,
        "sector_mapping_sha256": mapping.get("sector_mapping_sha256"),
        "rows_read": rows_read,
        "max_date": max_date,
        "prospective_rows_read": 0,
        "frozen_validation_rows_read": 0,
    }

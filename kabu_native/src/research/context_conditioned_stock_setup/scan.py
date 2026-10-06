"""Join PRIOR_BAR_HIGH_RECLAIM triggers to leave-target-out breadth."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np
import pyarrow.dataset as ds

import research.stock_specific_sequential_setup.episodes  # noqa: F401  (load dates before universe)
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.context_conditioned_stock_setup import C1_LAST, DEV_FIRST, FV_FIRST, SECTOR_MAPPING_SHA256, TRIGGER, UNIVERSE_SHA256
from research.context_conditioned_stock_setup.context import classify_trigger, day_flags
from research.stock_specific_sequential_setup.episodes import walk_session


def _sessions(minutes: np.ndarray) -> list[tuple[int, int]]:
    cuts = [0]
    for i in range(1, int(minutes.size)):
        if int(minutes[i]) - int(minutes[i - 1]) > 30:
            cuts.append(i)
    cuts.append(int(minutes.size))
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= 24]


def _clock(labels: list[str]) -> np.ndarray:
    return np.asarray([int(x[:2]) * 60 + int(x[3:5]) for x in labels], dtype=np.int32)


def scan() -> dict[str, Any]:
    universe = load_research_observation_universe()
    if universe.source_sha256 != UNIVERSE_SHA256:
        raise RuntimeError("universe_sha_mismatch")
    mapping = bind_sector_mapping()
    if mapping.get("sector_mapping_sha256") != SECTOR_MAPPING_SHA256 or not mapping.get("pass"):
        raise RuntimeError("sector_mapping_mismatch")
    sector_of = {str(row["symbol"]): str(row["sector_id"]) for row in mapping["rows"]}
    symbols = list(universe.ordered_symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    members: dict[str, list[int]] = defaultdict(list)
    for sym, sid in sector_of.items():
        if sym in index and sid:
            members[sid].append(index[sym])
    peers_of = {}
    for sid, rows in members.items():
        arr = np.asarray(rows, dtype=np.int32)
        for sym_i in rows:
            peers_of[sym_i] = arr[arr != sym_i]
    day_bars: dict[str, list[tuple[int, np.ndarray, np.ndarray]]] = defaultdict(list)
    triggers: list[tuple[str, int, int, Optional[float], Optional[float], Optional[float]]] = []
    dates: set[str] = set()
    max_date = ""
    rows_read = 0
    for nth, sym in enumerate(symbols, start=1):
        path = minute_parquet_path(sym)
        if not path.is_file():
            raise RuntimeError(f"missing_minute_parquet:{sym}")
        table = ds.dataset(str(path), format="parquet").to_table(
            columns=["date", "time_label", "open", "high", "low", "close", "volume", "trading_value"],
            filter=(ds.field("date") >= DEV_FIRST) & (ds.field("date") <= C1_LAST),
        )
        frame = table.to_pandas()
        if frame.empty:
            continue
        frame["date"] = frame["date"].astype(str)
        if bool(frame["date"].ge(FV_FIRST).any()):
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
            minutes = _clock(labels)
            opens = group["open"].to_numpy(dtype=float)
            highs = group["high"].to_numpy(dtype=float)
            lows = group["low"].to_numpy(dtype=float)
            closes = group["close"].to_numpy(dtype=float)
            volumes = group["volume"].to_numpy(dtype=float)
            traded = group["trading_value"].to_numpy(dtype=float)
            day_bars[day].append((sym_i, minutes.copy(), closes.copy()))
            for lo, hi in _sessions(minutes):
                for episode in walk_session(minutes[lo:hi], opens[lo:hi], highs[lo:hi], lows[lo:hi], closes[lo:hi], volumes[lo:hi], traded[lo:hi]):
                    for sig in episode["signals"]:
                        if sig["family"] != TRIGGER:
                            continue
                        triggers.append((day, sym_i, int(sig["minute"]), sig.get("ret_3"), sig.get("ret_5"), sig.get("ret_10")))
        if nth % 15 == 0 or nth == len(symbols):
            print(f"LOAD {nth}/{len(symbols)} triggers={len(triggers)} rows={rows_read}", flush=True)
    if max_date > C1_LAST or max_date >= FV_FIRST:
        raise RuntimeError("max_date_crossed_firewall")
    by_day: dict[str, list] = defaultdict(list)
    for row in triggers:
        by_day[row[0]].append(row)
    classified = {"ALIGNED": [], "NOT_ALIGNED": [], "SECTOR_CONTEXT_UNAVAILABLE": 0, "MARKET_CONTEXT_UNAVAILABLE": 0}
    for day in sorted(by_day):
        pieces = day_bars.get(day) or []
        if not pieces or not by_day[day]:
            continue
        all_minutes = np.unique(np.concatenate([mins for _i, mins, _px in pieces]))
        col = {int(m): j for j, m in enumerate(all_minutes.tolist())}
        closes = np.full((len(symbols), int(all_minutes.size)), np.nan)
        for sym_i, mins, px in pieces:
            closes[sym_i, [col[int(m)] for m in mins.tolist()]] = px
        flags = day_flags(closes, all_minutes)
        for _day, sym_i, minute, r3, r5, r10 in by_day[day]:
            column = col.get(int(minute))
            if column is None:
                classified["MARKET_CONTEXT_UNAVAILABLE"] += 1
                continue
            got = classify_trigger(
                flags,
                closes,
                symbol=sym_i,
                column=int(column),
                peers=peers_of.get(sym_i, np.asarray([], dtype=np.int32)),
                ret5_bps=None if r5 is None else float(r5),
            )
            state = got["state"]
            if state in ("SECTOR_CONTEXT_UNAVAILABLE", "MARKET_CONTEXT_UNAVAILABLE"):
                classified[state] += 1
                continue
            classified[state].append(
                {
                    "date": day,
                    "symbol": symbols[sym_i],
                    "sector": sector_of.get(symbols[sym_i]),
                    "ret3": r3,
                    "ret5": r5,
                    "ret10": r10,
                    "relative_ret5": got.get("relative_ret5_bps"),
                }
            )
        if len(by_day) and (len([d for d in sorted(by_day) if d <= day]) % 40 == 0 or day == max(by_day)):
            print(f"CONTEXT {day} aligned={len(classified['ALIGNED'])} other={len(classified['NOT_ALIGNED'])}", flush=True)
    return {
        "universe_sha256": universe.source_sha256,
        "universe_n": universe.symbol_count,
        "symbols_order_sha256": universe.symbols_order_sha256,
        "sector_mapping_sha256": mapping["sector_mapping_sha256"],
        "sector_n": mapping["sector_n"],
        "rows_read": rows_read,
        "max_date": max_date,
        "min_date": min(dates) if dates else None,
        "session_dates": sorted(dates),
        "trigger_n": len(triggers),
        "aligned": classified["ALIGNED"],
        "not_aligned": classified["NOT_ALIGNED"],
        "sector_unavailable_n": classified["SECTOR_CONTEXT_UNAVAILABLE"],
        "market_unavailable_n": classified["MARKET_CONTEXT_UNAVAILABLE"],
        "prospective_rows_read": 0,
        "fv_rows_read": 0,
    }

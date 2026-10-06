"""Exposed capture confirmation. Runs only after the historical gates pass."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.relative_strength_family_d import CAPTURE_FIRST, CAPTURE_LAST
from research.relative_strength_family_d.evaluate import evaluate_day
from research.simple_tech_entry_family.bars import SymbolBarBuilder
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.symbol_setup_failure_evidence_gap.scan import _decompose, _joint_mask, _next_ok

HORIZONS = (30, 60, 180, 300)


def _dates() -> tuple[str, ...]:
    dates = tuple(sorted(set(ORIGINAL18 + EXTENSION17)))
    if dates[0] != CAPTURE_FIRST or dates[-1] != CAPTURE_LAST:
        raise RuntimeError("capture_window_mismatch")
    if any(day < CAPTURE_FIRST or day > CAPTURE_LAST or day >= "20260911" for day in dates):
        raise RuntimeError("capture_date_outside_exposed_window")
    return dates


def _minutes(epoch: np.ndarray) -> np.ndarray:
    local = np.asarray(epoch, dtype=float) + 9.0 * 3600.0
    return np.floor((local % 86400.0) / 60.0).astype(int)


def _stat(xs: list[float]) -> dict[str, Any]:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "positive_fraction": None, "zero_fraction": None, "negative_fraction": None}
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "positive_fraction": float(np.mean(arr > 0)),
        "zero_fraction": float(np.mean(arr == 0)),
        "negative_fraction": float(np.mean(arr < 0)),
    }


def _session_end(day: str, minute: int) -> float:
    if minute < 11 * 60 + 30:
        return float(hm_epoch(day, 11, 30))
    return float(hm_epoch(day, 15, 0))


def _day(day: str, capture: Path, symbols: list[str], peers_of: dict[int, np.ndarray], sector_of: dict[int, str]) -> list[dict[str, Any]]:
    start = float(hm_epoch(day, 9, 0))
    end = float(hm_epoch(day, 15, 0))
    index = {sym: i for i, sym in enumerate(symbols)}
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=start, am_end=end) for s in symbols}
    push_n = 0
    for rec in iter_push(capture):
        push_n += 1
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in bufs:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < start - 120.0 or float(et) > end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        row = _board_row(pay, float(et))
        bufs[sym].append(row)
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
    pieces = []
    books = {}
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        if int(raw["minute_epoch"].size) < 6:
            continue
        minutes = _minutes(raw["minute_epoch"])
        sym_i = index[sym]
        pieces.append((sym_i, minutes, raw["high"].astype(float), raw["low"].astype(float), raw["close"].astype(float), raw["volume"].astype(float)))
        board = bufs[sym].view()
        books[sym_i] = (raw, board, _next_ok(_joint_mask(board)))
    events, _counts = evaluate_day(day, len(symbols), pieces, peers_of, sector_of)
    out = []
    for event in events:
        if event["population"] != "FULL":
            continue
        raw, board, nxt = books[int(event["symbol"])]
        bar = int(event["local"])
        t0 = float(raw["finalize_t"][bar])
        decomposed, _fails = _decompose(board, nxt, raw, bar, t0, _session_end(day, int(event["minute"])))
        horizons = decomposed.get("horizons") or {}
        packed = {"date": day, "symbol": symbols[int(event["symbol"])], "push_records": push_n}
        for horizon in HORIZONS:
            cell = horizons.get(str(horizon)) or {}
            packed[f"raw_{horizon}"] = cell.get("raw_mid_bps")
            packed[f"bid_{horizon}"] = cell.get("bid_anchor_bps")
            packed[f"atb_{horizon}"] = cell.get("ask_to_bid_bps")
        out.append(packed)
    return out


def _folds(dates: tuple[str, ...]) -> dict[str, int]:
    ordered = list(dates)
    cuts = [int(round(i * len(ordered) / 3)) for i in range(4)]
    out = {}
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for one in ordered[lo:hi]:
            out[one] = fold
    return out


def confirm() -> dict[str, Any]:
    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    members: dict[str, list[int]] = defaultdict(list)
    sector_of: dict[int, str] = {}
    for row in mapping["rows"]:
        sym = str(row["symbol"])
        sid = str(row.get("sector_id") or "")
        if sym in index and sid:
            sector_of[index[sym]] = sid
            members[sid].append(index[sym])
    peers_of = {}
    for rows in members.values():
        arr = np.asarray(rows, dtype=np.int32)
        for sym_i in rows:
            peers_of[int(sym_i)] = arr[arr != int(sym_i)]
    rows: list[dict[str, Any]] = []
    for day in dates:
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), symbols, peers_of, sector_of)
        rows.extend(one)
        print(f"CAPTURE {day} full={len(one)}", flush=True)
    summary: dict[str, Any] = {
        "ran": True,
        "n": len(rows),
        "max_capture_date_read": max(dates) if dates else None,
        "dates_read": list(dates),
    }
    for name, key in (("raw_mid", "raw"), ("bid_anchor", "bid"), ("ask_to_bid", "atb")):
        summary[name] = {str(h): _stat([row[f"{key}_{h}"] for row in rows]) for h in HORIZONS}
    for name, days in (("ORIGINAL18", set(ORIGINAL18)), ("EXTENSION17", set(EXTENSION17))):
        group = [row for row in rows if row["date"] in days]
        summary[name] = {"n": len(group), "ask_180": _stat([row["atb_180"] for row in group])}
    fold_of = _folds(dates)
    fold_rows = []
    for fold in range(3):
        group = [row for row in rows if fold_of.get(row["date"]) == fold]
        stat = _stat([row["atb_180"] for row in group])
        fold_rows.append({"fold": fold, "n": len(group), **stat, "mean_positive": bool(stat["mean"] is not None and stat["mean"] > 0)})
    summary["folds"] = fold_rows
    return summary

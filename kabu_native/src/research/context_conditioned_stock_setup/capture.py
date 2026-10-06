"""Exposed-capture confirmation for aligned triggers only. No date after 20260910."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping
from research.context_conditioned_stock_setup import CAPTURE_FIRST, CAPTURE_LAST, TRIGGER
from research.context_conditioned_stock_setup.context import classify_trigger, day_flags
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.simple_tech_entry_family.stages import attach_indicators
from research.stock_specific_sequential_setup.episodes import walk_session
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.symbol_setup_failure_evidence_gap.scan import _decompose, _joint_mask, _next_ok
from small_paper.v1r_live_dual_lane import session_end_for_position

HORIZONS = (30, 60, 180, 300)


def _dates() -> tuple[str, ...]:
    dates = tuple(sorted(set(ORIGINAL18 + EXTENSION17)))
    if any(day < CAPTURE_FIRST or day > CAPTURE_LAST or day >= "20260911" for day in dates):
        raise RuntimeError("capture_date_outside_exposed_window")
    return dates


def _minutes(epoch: np.ndarray) -> np.ndarray:
    local = np.asarray(epoch, dtype=float) + 9.0 * 3600.0
    return np.floor((local % 86400.0) / 60.0).astype(int)


def _stat(xs: list[Optional[float]]) -> dict[str, Any]:
    arr = np.asarray([np.nan if x is None else float(x) for x in xs], dtype=float)
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


def _day(day: str, capture: Path, symbols: list[str], peers_of: dict[int, np.ndarray]) -> list[dict[str, Any]]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    index = {sym: i for i, sym in enumerate(symbols)}
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    seen = set(symbols)
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in seen:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
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
    prepared = []
    triggers = []
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        if int(raw["minute_epoch"].size) < 24:
            continue
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        ind = attach_indicators(raw)
        minutes = _minutes(ind["minute_epoch"])
        prepared.append((index[sym], minutes, ind["close"].astype(float)))
        board = bufs[sym].view()
        nxt = _next_ok(_joint_mask(board))
        for episode in walk_session(minutes, ind["open"], ind["high"], ind["low"], ind["close"], ind["volume"], ind["close"] * ind["volume"]):
            for sig in episode["signals"]:
                if sig["family"] != TRIGGER:
                    continue
                triggers.append((index[sym], int(sig["minute"]), int(sig["bar"]), ind, board, nxt))
    if not prepared or not triggers:
        return []
    all_minutes = np.unique(np.concatenate([mins for _i, mins, _px in prepared]))
    col = {int(m): j for j, m in enumerate(all_minutes.tolist())}
    closes = np.full((len(symbols), int(all_minutes.size)), np.nan)
    for sym_i, mins, px in prepared:
        closes[sym_i, [col[int(m)] for m in mins.tolist()]] = px
    flags = day_flags(closes, all_minutes)
    out = []
    for sym_i, minute, bar, ind, board, nxt in triggers:
        column = col.get(minute)
        if column is None:
            continue
        got = classify_trigger(flags, closes, symbol=sym_i, column=int(column), peers=peers_of.get(sym_i, np.asarray([], dtype=np.int32)), ret5_bps=None)
        if got["state"] != "ALIGNED":
            continue
        t0 = float(ind["finalize_t"][bar])
        decomposed, _fails = _decompose(board, nxt, ind, bar, t0, am_end)
        horizons = decomposed.get("horizons") or {}
        packed = {"date": day, "symbol": symbols[sym_i]}
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
        for day in ordered[lo:hi]:
            out[day] = fold
    return out


def confirm() -> dict[str, Any]:
    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    members: dict[str, list[int]] = defaultdict(list)
    for row in mapping["rows"]:
        sym = str(row["symbol"])
        if sym in index and row.get("sector_id"):
            members[str(row["sector_id"])].append(index[sym])
    peers_of = {}
    for rows in members.values():
        arr = np.asarray(rows, dtype=np.int32)
        for sym_i in rows:
            peers_of[sym_i] = arr[arr != sym_i]
    rows = []
    for day in dates:
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), symbols, peers_of)
        rows.extend(one)
        print(f"CAPTURE {day} aligned={len(one)}", flush=True)
    summary: dict[str, Any] = {"ran": True, "n": len(rows), "max_capture_date_read": max(dates), "family": TRIGGER}
    for name, key in (("raw_mid", "raw"), ("bid_anchor", "bid"), ("ask_to_bid", "atb")):
        summary[name] = {str(h): _stat([row[f"{key}_{h}"] for row in rows]) for h in HORIZONS}
    groups = {"ORIGINAL18": set(ORIGINAL18), "EXTENSION17": set(EXTENSION17)}
    for name, days in groups.items():
        group = [row for row in rows if row["date"] in days]
        summary[name] = {"n": len(group), "bid_180": _stat([row["bid_180"] for row in group]), "raw_180": _stat([row["raw_180"] for row in group])}
    fold_of = _folds(dates)
    fold_rows = []
    positive = 0
    for fold in range(3):
        group = [row for row in rows if fold_of.get(row["date"]) == fold]
        stat = _stat([row["bid_180"] for row in group])
        ok = stat["n"] > 0 and stat["mean"] is not None and stat["mean"] > 0
        positive += int(ok)
        fold_rows.append({"fold": fold, "n": len(group), **{f"bid_{k}": stat[k] for k in ("mean", "median", "positive_fraction", "zero_fraction", "negative_fraction")}})
    summary["folds"] = fold_rows
    raw = summary["raw_mid"]["180"]
    bid = summary["bid_anchor"]["180"]
    gates = {
        "raw_mean": raw["mean"] is not None and raw["mean"] > 0,
        "bid_mean": bid["mean"] is not None and bid["mean"] > 0,
        "bid_positive_gt_negative": bid["positive_fraction"] is not None and bid["negative_fraction"] is not None and bid["positive_fraction"] > bid["negative_fraction"],
        "original18": (summary["ORIGINAL18"]["raw_180"]["mean"] or -1) >= 0 and (summary["ORIGINAL18"]["bid_180"]["mean"] or -1) >= 0,
        "extension17": (summary["EXTENSION17"]["raw_180"]["mean"] or -1) >= 0 and (summary["EXTENSION17"]["bid_180"]["mean"] or -1) >= 0,
        "folds": positive >= 2,
    }
    summary["gates"] = gates
    summary["pass"] = all(gates.values())
    return summary

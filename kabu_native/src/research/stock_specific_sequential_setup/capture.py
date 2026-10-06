"""Rebuild one selected state on the already exposed capture window. No new dates."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.simple_tech_entry_family.stages import attach_indicators
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.symbol_setup_baseline_complete_strategy_precommit.universe import recover
from research.symbol_setup_failure_evidence_gap.scan import _decompose, _joint_mask, _next_ok
from research.stock_specific_sequential_setup import CAPTURE_FIRST, CAPTURE_LAST
from research.stock_specific_sequential_setup.episodes import walk_session
from small_paper.v1r_live_dual_lane import session_end_for_position

HORIZONS = (30, 60, 180, 300)


def _median(xs: list[float]) -> Optional[float]:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(np.median(arr))


def _minutes(epoch: np.ndarray) -> np.ndarray:
    local = np.asarray(epoch, dtype=float) + 9.0 * 3600.0
    return np.floor((local % 86400.0) / 60.0).astype(int)


def _day(day: str, capture: Path, symbols: list[str], family: str) -> list[dict[str, Any]]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    uni = set(symbols)
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
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
    out = []
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        if int(raw["minute_epoch"].size) < 24:
            continue
        ind = attach_indicators(raw)
        board = bufs[sym].view()
        nxt = _next_ok(_joint_mask(board))
        minutes = _minutes(ind["minute_epoch"])
        walked = walk_session(minutes, ind["open"], ind["high"], ind["low"], ind["close"], ind["volume"], ind["close"] * ind["volume"])
        for episode in walked:
            for sig in episode["signals"]:
                if sig["family"] != family:
                    continue
                bar = int(sig["bar"])
                t0 = float(ind["finalize_t"][bar])
                decomposed, _fails = _decompose(board, nxt, ind, bar, t0, am_end)
                cell = (decomposed.get("horizons") or {}).get(180) or (decomposed.get("horizons") or {}).get("180")
                horizons = decomposed.get("horizons") or {}
                packed = {"date": day, "symbol": sym}
                for horizon in HORIZONS:
                    got = horizons.get(horizon) or horizons.get(str(horizon)) or {}
                    packed[f"raw_{horizon}"] = got.get("raw_mid_bps")
                    packed[f"bid_{horizon}"] = got.get("bid_anchor_bps")
                    packed[f"atb_{horizon}"] = got.get("ask_to_bid_bps")
                _ = cell
                out.append(packed)
    return out


def _folds(dates: list[str]) -> dict[str, int]:
    ordered = sorted(set(dates))
    cuts = [int(round(i * len(ordered) / 3)) for i in range(4)]
    out = {}
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for day in ordered[lo:hi]:
            out[day] = fold
    return out


def confirm(family: str) -> dict[str, Any]:
    dates = tuple(sorted(set(ORIGINAL18 + EXTENSION17)))
    if any(day < CAPTURE_FIRST or day > CAPTURE_LAST or day >= "20260911" for day in dates):
        raise RuntimeError("capture_date_outside_exposed_window")
    recovered = recover(list(dates))
    rows = []
    for sess in recovered["sessions"]:
        day = str(sess["date"])
        if not sess.get("resolved") or not sess.get("capture_present"):
            raise RuntimeError(f"capture_universe_unresolved:{day}")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        one = _day(day, Path(cap), list(sess["symbols"]), family)
        rows.extend(one)
        print(f"CAPTURE {day} signals={len(one)}", flush=True)
    fold_of = _folds(list(dates))
    summary = {"family": family, "n": len(rows), "ran": True}
    for horizon in HORIZONS:
        summary[f"raw_mid_{horizon}"] = _median([row[f"raw_{horizon}"] for row in rows])
        summary[f"bid_anchor_{horizon}"] = _median([row[f"bid_{horizon}"] for row in rows])
        summary[f"ask_to_bid_{horizon}"] = _median([row[f"atb_{horizon}"] for row in rows])
        summary[f"covered_{horizon}"] = sum(1 for row in rows if row[f"raw_{horizon}"] is not None)
    groups = {
        "ORIGINAL18": [row for row in rows if row["date"] in set(ORIGINAL18)],
        "EXTENSION17": [row for row in rows if row["date"] in set(EXTENSION17)],
    }
    for name, group in groups.items():
        summary[f"{name}_n"] = len(group)
        summary[f"{name}_raw_180"] = _median([row["raw_180"] for row in group])
        summary[f"{name}_bid_180"] = _median([row["bid_180"] for row in group])
    fold_rows = []
    positive = 0
    for fold in range(3):
        group = [row for row in rows if fold_of.get(row["date"]) == fold]
        raw = _median([row["raw_180"] for row in group])
        bid = _median([row["bid_180"] for row in group])
        ok = len(group) >= 10 and raw is not None and bid is not None and raw > 0 and bid > 0
        positive += int(ok)
        fold_rows.append({"fold": fold, "n": len(group), "raw_180": raw, "bid_180": bid, "positive": ok})
    summary["folds"] = fold_rows
    raw180 = summary["raw_mid_180"]
    bid180 = summary["bid_anchor_180"]
    gates = {
        "raw_180_positive": raw180 is not None and raw180 > 0,
        "bid_180_positive": bid180 is not None and bid180 > 0,
        "original18_non_negative": summary["ORIGINAL18_raw_180"] is not None and summary["ORIGINAL18_raw_180"] >= 0 and summary["ORIGINAL18_bid_180"] is not None and summary["ORIGINAL18_bid_180"] >= 0,
        "extension17_non_negative": summary["EXTENSION17_raw_180"] is not None and summary["EXTENSION17_raw_180"] >= 0 and summary["EXTENSION17_bid_180"] is not None and summary["EXTENSION17_bid_180"] >= 0,
        "folds": positive >= 2,
        "covered": int(summary["covered_180"]) >= 30,
    }
    summary["gates"] = gates
    summary["pass"] = all(gates.values())
    summary["failed_gate"] = next((key for key, ok in gates.items() if not ok), None)
    return summary

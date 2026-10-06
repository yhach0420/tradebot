"""Frozen V2 replay plus fixed-landmark features. Does not change V2."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_first_ratchet_transition_audit_v1.features import TIME_LANDMARKS_MS, window_features, t0_parent
from research.v2_genuine_resistance_gate_complete_strategy_v1.gate import genuine_resistance_at_signal
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.identity_sim import simulate_with_signal_index
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.observer import observe_from_signal


def _times(observed: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    first = None
    exit_t = None
    for event in observed["events"]:
        if event["decision"] == "ratchet" and first is None:
            first = float(event["event_t"])
        if event["decision"] == "exit":
            exit_t = float(event["event_t"])
            break
    return first, exit_t


def _status(first: Optional[float], exit_decision: Optional[float], frozen_exit: float, t0: float, horizon_s: float) -> str:
    limit = t0 + horizon_s
    if first is not None and first <= limit + 1e-9:
        return "ALREADY_RATCHETED"
    end = exit_decision if exit_decision is not None else frozen_exit
    if end is not None and end <= limit + 1e-9:
        return "ALREADY_EXITED"
    return "STILL_UNRESOLVED"


def _maybe_chart(charts: list[dict[str, Any]], book: dict[str, Any], row: dict[str, Any], kind: str) -> None:
    if sum(1 for item in charts if item["kind"] == kind) >= 4:
        return
    if any(item["kind"] == kind and (item["date"] == row["date"] or item["symbol"] == row["symbol"]) for item in charts):
        return
    t0 = float(row["entry_t"]) - 60.0
    t1 = float(row["exit_t"]) + 5.0
    times = book["t"]
    left = int(np.searchsorted(times, t0, side="left"))
    right = int(np.searchsorted(times, t1, side="right"))
    bt = book["board_t"]
    bleft = int(np.searchsorted(bt, t0, side="left"))
    bright = int(np.searchsorted(bt, t1, side="right"))
    step = max(1, (right - left) // 2500)
    bstep = max(1, (bright - bleft) // 2500)
    charts.append({
        "kind": kind,
        "symbol": row["symbol"],
        "date": row["date"],
        "entry_t": float(row["entry_t"]),
        "exit_t": float(row["exit_t"]),
        "entry_px": float(row["entry_px"]),
        "exit_px": float(row["exit_px"]),
        "reason": row["reason"],
        "pnl_yen": float(row["pnl_yen"]),
        "ratchets": int(row["ratchets"]),
        "pre_break": float(book["pre_high"][int(row["signal_index"])]),
        "first_ratchet_t": row.get("first_ratchet_t"),
        "t": times[left:right:step].astype(float).tolist(),
        "px": book["px"][left:right:step].astype(float).tolist(),
        "bt": bt[bleft:bright:bstep].astype(float).tolist(),
        "bid": book["board_bid"][bleft:bright:bstep].astype(float).tolist(),
        "ask": book["board_ask"][bleft:bright:bstep].astype(float).tolist(),
    })


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    rows: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    uids: list[str] = []
    uids2: list[str] = []
    observer_mismatch = 0
    identity_miss = 0
    push_records = 0
    for day in dates:
        if day >= "20260911":
            raise RuntimeError(f"refused_date:{day}")
        capture = find_capture_dir(day)
        if capture is None:
            raise RuntimeError(f"missing_capture:{day}")
        am0 = float(hm_epoch(day, AM_START[0], AM_START[1]))
        am1 = float(hm_epoch(day, AM_END[0], AM_END[1]))
        pm0 = float(hm_epoch(day, PM_START[0], PM_START[1]))
        pm1 = float(hm_epoch(day, PM_END[0], PM_END[1]))
        bufs = {sym: _Buf() for sym in symbols}
        cols = {sym: {"AM": _Col(), "PM": _Col()} for sym in symbols}
        last_cum: dict[str, Optional[float]] = {sym: None for sym in symbols}
        for rec in iter_push(Path(capture)):
            push_records += 1
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if sym not in seen:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            event_t = float(et)
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            board_row = _board_row(pay, event_t)
            if event_t < am0 - 120.0 or event_t > pm1 + 2.0:
                continue
            bufs[sym].append(board_row)
            cum = board_row["cum_vol"]
            dvol = 0.0
            if cum is not None and cum == cum and cum >= 0:
                if last_cum[sym] is not None and cum >= float(last_cum[sym]):
                    dvol = float(cum - float(last_cum[sym]))
                last_cum[sym] = float(cum)
            if not board_row["continuous"]:
                continue
            if am0 <= event_t < am1:
                session = "AM"
            elif pm0 <= event_t < pm1:
                session = "PM"
            else:
                continue
            price = float(board_row["px"])
            finite_px = price == price and price > 0
            ask_vol, bid_vol = _classify(price, float(board_row["bid"]), float(board_row["ask"]), dvol)
            fresh = _quote_ok(board_row)
            cols[sym][session].add(
                t=event_t, px=price if finite_px else np.nan, vol=dvol, ask=ask_vol, bid=bid_vol,
                tick=1.0 if finite_px else 0.0,
                spread=(float(board_row["ask"]) - float(board_row["bid"])) if fresh else np.nan,
                ok=fresh, ask_px=float(board_row["ask"]) if fresh else np.nan,
            )
        day_full = 0
        traded_base: set[str] = set()
        traded_id: set[str] = set()
        for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
            books: dict[str, Any] = {}
            raw_arrays: dict[str, dict[str, np.ndarray]] = {}
            for sym in symbols:
                arrays, ask_px = _arrays(cols[sym][session])
                if int(arrays["t"].size) == 0:
                    continue
                feat = evaluate(arrays["t"], arrays["px"], arrays["vol"], arrays["ask"], arrays["bid"], arrays["tick"], start)
                signals = _full_signals(feat, arrays)
                day_full += int(signals.size)
                if int(signals.size) == 0:
                    continue
                board = bufs[sym].view()
                book = prepare_book(feat, arrays, board, signals, ask_px)
                book["board_t"] = board["t"]
                book["board_bid"] = board["bid"]
                book["board_ask"] = board["ask"]
                book["board_fresh"] = board["fresh_sec"]
                book["ask_vol"] = arrays["ask"]
                book["bid_vol"] = arrays["bid"]
                book["event_vol"] = arrays["vol"]
                books[sym] = book
                raw_arrays[sym] = arrays
                for source in signals:
                    uids.append(f"{day}|{session}|{sym}|{int(source)}")
            for sym, book in books.items():
                for source in book["signals"]:
                    uids2.append(f"{day}|{session}|{sym}|{int(source)}")
            admits = {sym: genuine_resistance_at_signal(book) for sym, book in books.items()}
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded_base)
            identified = simulate_with_signal_index(books, day=day, session=session, sess_end=end, traded_today=traded_id)
            frozen_rows = [row for row in got["trades"] if row.get("pnl_yen") is not None]
            id_rows = identified["trades"]
            if len(frozen_rows) != len(id_rows):
                identity_miss += abs(len(frozen_rows) - len(id_rows))
            for row, ident in zip(frozen_rows, id_rows):
                book = books[row["symbol"]]
                same = (
                    abs(float(row["entry_t"]) - float(ident["entry_t"])) < 1e-9
                    and abs(float(row["exit_t"]) - float(ident["exit_t"])) < 1e-6
                    and int(row["ratchets"]) == int(ident["ratchets"])
                    and str(row["reason"]) == str(ident["reason"])
                )
                if not same:
                    identity_miss += 1
                    continue
                index = int(ident["signal_index"])
                observed = observe_from_signal(book, index)
                if int(observed["ratchet_n"]) != int(row["ratchets"]):
                    observer_mismatch += 1
                first, exit_decision = _times(observed)
                if int(row["ratchets"]) == 0:
                    first = None
                entry_t = float(row["entry_t"])
                record = {
                    "date": day,
                    "session": session,
                    "symbol": row["symbol"],
                    "signal_uid": f"{day}|{session}|{row['symbol']}|{index}",
                    "signal_index": index,
                    "ratchets": int(row["ratchets"]),
                    "label": "RATCHET_GE1" if int(row["ratchets"]) >= 1 else "RATCHET_0",
                    "pnl_yen": float(row["pnl_yen"]),
                    "bps": float(row["bps"]),
                    "entry_t": entry_t,
                    "exit_t": float(row["exit_t"]),
                    "first_ratchet_t": first,
                    "exit_decision_t": exit_decision,
                    "genuine": bool(admits[row["symbol"]].get(index, False)),
                    "t0": t0_parent(book, index),
                    "landmarks": {},
                }
                for ms in TIME_LANDMARKS_MS:
                    horizon = ms / 1000.0
                    state = _status(first, exit_decision, float(row["exit_t"]), entry_t, horizon)
                    feats = window_features(book, index, horizon_s=horizon, event_n=None) if state == "STILL_UNRESOLVED" or ms == 0 else None
                    record["landmarks"][str(ms)] = {"status": state, "features": feats}
                rows.append(record)
                row["signal_index"] = index
                row["first_ratchet_t"] = first
                hold = float(row["exit_t"]) - entry_t
                if int(row["ratchets"]) == 0 and hold < 1.0:
                    _maybe_chart(charts, book, row, "r0_early")
                elif int(row["ratchets"]) == 0 and hold >= 30.0:
                    _maybe_chart(charts, book, row, "r0_long")
                elif first is not None and first - entry_t < 0.25:
                    _maybe_chart(charts, book, row, "ge1_early")
                elif first is not None and first - entry_t >= 2.0:
                    _maybe_chart(charts, book, row, "ge1_late")
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} rows={sum(1 for r in rows if r['date']==day)}", flush=True)
    return {
        "rows": rows,
        "charts": charts,
        "uids": uids,
        "uids2": uids2,
        "observer_mismatch": observer_mismatch,
        "identity_miss": identity_miss,
        "dates": list(dates),
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
        "push_records": push_records,
    }

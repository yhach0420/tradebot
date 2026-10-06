"""Frozen V2 replay plus causal ratchet-state measurement. Does not edit V2."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_impulse_v2_robustness_audit.latency import attach_quote_index
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.identity_sim import simulate_with_signal_index
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.observer import observe_from_signal
from research.v2_ratchet_state_transition_value_audit_v1.states import state_records, walk_position


def _kind(ratchets: int) -> str | None:
    if ratchets == 0:
        return "ratchet0"
    if ratchets == 1:
        return "ratchet1"
    if ratchets == 2:
        return "ratchet2"
    if ratchets >= 3:
        return "ratchet3plus"
    return None


def _maybe_chart(charts: list[dict[str, Any]], book: dict[str, Any], trade: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    kind = _kind(int(trade["ratchets"]))
    if kind is None or sum(1 for item in charts if item["kind"] == kind) >= 4:
        return
    if any(item["kind"] == kind and (item["date"] == trade["date"] or item["symbol"] == trade["symbol"]) for item in charts):
        return
    t0 = float(trade["entry_t"]) - 60.0
    t1 = float(trade["exit_t"]) + 5.0
    times = book["t"]
    left = int(np.searchsorted(times, t0, side="left"))
    right = int(np.searchsorted(times, t1, side="right"))
    bt = book["board_t"]
    bleft = int(np.searchsorted(bt, t0, side="left"))
    bright = int(np.searchsorted(bt, t1, side="right"))
    step = max(1, (right - left) // 2500)
    bstep = max(1, (bright - bleft) // 2500)
    charts.append(
        {
            "kind": kind,
            "symbol": trade["symbol"],
            "date": trade["date"],
            "entry_t": float(trade["entry_t"]),
            "exit_t": float(trade["exit_t"]),
            "entry_px": float(trade["entry_px"]),
            "exit_px": float(trade["exit_px"]),
            "reason": trade["reason"],
            "pnl_yen": float(trade["pnl_yen"]),
            "ratchets": int(trade["ratchets"]),
            "t": times[left:right:step].astype(float).tolist(),
            "px": book["px"][left:right:step].astype(float).tolist(),
            "bt": bt[bleft:bright:bstep].astype(float).tolist(),
            "bid": book["board_bid"][bleft:bright:bstep].astype(float).tolist(),
            "ask": book["board_ask"][bleft:bright:bstep].astype(float).tolist(),
            "states": [
                {
                    "state": int(r["state"]),
                    "t": float(r["state_t"]),
                    "support": float(r["support"]),
                    "exit_now_yen": r["exit_now_yen"],
                    "final_yen": r["final_yen"],
                    "continue_yen": r["continue_yen"],
                }
                for r in rows
            ],
        }
    )


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = _dates()
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    baseline: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    uids: list[str] = []
    uids2: list[str] = []
    observer_mismatch = 0
    identity_miss = 0
    walk_mismatch = 0
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
        last_cum: dict[str, float | None] = {sym: None for sym in symbols}
        for rec in iter_push(Path(capture)):
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
        traded = {"base": set(), "id": set()}
        for session, end in (("AM", am1), ("PM", pm1)):
            books: dict[str, Any] = {}
            for sym in symbols:
                arrays, ask_px = _arrays(cols[sym][session])
                if int(arrays["t"].size) == 0:
                    continue
                feat = evaluate(arrays["t"], arrays["px"], arrays["vol"], arrays["ask"], arrays["bid"], arrays["tick"], am0 if session == "AM" else pm0)
                signals = _full_signals(feat, arrays)
                day_full += int(signals.size)
                if int(signals.size) == 0:
                    continue
                board = bufs[sym].view()
                book = prepare_book(feat, arrays, board, signals, ask_px)
                attach_quote_index(book, board)
                books[sym] = book
                for source in signals:
                    uids.append(f"{day}|{session}|{sym}|{int(source)}")
            for sym, book in books.items():
                for source in book["signals"]:
                    uids2.append(f"{day}|{session}|{sym}|{int(source)}")
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded["base"])
            identified = simulate_with_signal_index(books, day=day, session=session, sess_end=end, traded_today=traded["id"])
            frozen_rows = [row for row in got["trades"] if row.get("pnl_yen") is not None]
            id_rows = identified["trades"]
            if len(frozen_rows) != len(id_rows):
                identity_miss += abs(len(frozen_rows) - len(id_rows))
            for row, ident in zip(frozen_rows, id_rows):
                if int(row["ratchets"]) != int(ident["ratchets"]) or str(row["reason"]) != str(ident["reason"]) or abs(float(row["entry_t"]) - float(ident["entry_t"])) > 1e-9 or abs(float(row["entry_px"]) - float(ident["entry_px"])) > 1e-6:
                    identity_miss += 1
                    continue
                index = int(ident["signal_index"])
                book = books[row["symbol"]]
                observed = observe_from_signal(book, index)
                if int(observed["ratchet_n"]) != int(row["ratchets"]):
                    observer_mismatch += 1
                walked = walk_position(book, index)
                if int(walked["ratchet_n"]) != int(row["ratchets"]):
                    walk_mismatch += 1
                row["signal_index"] = index
                row["signal_uid"] = f"{day}|{session}|{row['symbol']}|{index}"
                baseline.append(row)
                measured = state_records(book, walked, row, end)
                records.extend(measured)
                _maybe_chart(charts, book, row, measured)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} trades={sum(1 for r in baseline if r['date']==day)}", flush=True)
    return {
        "baseline": baseline,
        "records": records,
        "charts": charts,
        "uids": uids,
        "uids2": uids2,
        "observer_mismatch": observer_mismatch,
        "identity_miss": identity_miss,
        "walk_mismatch": walk_mismatch,
        "dates": list(dates),
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
    }

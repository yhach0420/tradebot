"""Baseline frozen V2 plus the 500ms no-bid-uptick abort. Does not edit V2."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_impulse_v2_robustness_audit.latency import attach_quote_index, simulate_latency
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_500ms_no_bid_uptick_early_abort_v1.abort_sim import ABORT_SEC, fresh_bid, simulate_abort
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.identity_sim import simulate_with_signal_index
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.observer import observe_from_signal


def _upticks(book: dict[str, Any], signal_index: int, signal_t: float) -> int:
    times = book["t"]
    prev = fresh_bid(book, signal_t)
    count = 0
    i = int(signal_index) + 1
    limit = signal_t + ABORT_SEC
    n = int(times.size)
    while i < n and float(times[i]) <= limit + 1e-12:
        bid = fresh_bid(book, float(times[i]))
        if bid is not None:
            if prev is not None and bid > prev:
                count += 1
            prev = bid
        i += 1
    return count


def _transition(observed: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    first = None
    exit_t = None
    for event in observed["events"]:
        if event["decision"] == "ratchet" and first is None:
            first = float(event["event_t"])
        if event["decision"] == "exit":
            exit_t = float(event["event_t"])
            break
    return first, exit_t


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = _dates()
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    baseline_100: list[dict[str, Any]] = []
    candidate_100: list[dict[str, Any]] = []
    population: list[dict[str, Any]] = []
    uids: list[str] = []
    uids2: list[str] = []
    observer_mismatch = 0
    identity_miss = 0
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
        traded = {"base": set(), "id": set(), "c0": set(), "b100": set(), "c100": set()}
        for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
            books: dict[str, Any] = {}
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
                attach_quote_index(book, board)
                books[sym] = book
                for source in signals:
                    uids.append(f"{day}|{session}|{sym}|{int(source)}")
            for sym, book in books.items():
                for source in book["signals"]:
                    uids2.append(f"{day}|{session}|{sym}|{int(source)}")
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded["base"])
            identified = simulate_with_signal_index(books, day=day, session=session, sess_end=end, traded_today=traded["id"])
            cand = simulate_abort(books, day=day, session=session, sess_end=end, traded_today=traded["c0"], latency_sec=0.0, fill_model="asof")
            lat_b = simulate_latency(books, day=day, session=session, sess_end=end, traded_today=traded["b100"], latency_sec=0.1, fill_model="asof")
            lat_c = simulate_abort(books, day=day, session=session, sess_end=end, traded_today=traded["c100"], latency_sec=0.1, fill_model="asof")
            frozen_rows = [row for row in got["trades"] if row.get("pnl_yen") is not None]
            id_rows = identified["trades"]
            if len(frozen_rows) != len(id_rows):
                identity_miss += abs(len(frozen_rows) - len(id_rows))
            for row, ident in zip(frozen_rows, id_rows):
                book = books[row["symbol"]]
                if int(row["ratchets"]) != int(ident["ratchets"]) or str(row["reason"]) != str(ident["reason"]):
                    identity_miss += 1
                    continue
                index = int(ident["signal_index"])
                observed = observe_from_signal(book, index)
                if int(observed["ratchet_n"]) != int(row["ratchets"]):
                    observer_mismatch += 1
                first, exit_decision = _transition(observed)
                signal_t = float(row["entry_t"])
                limit = signal_t + ABORT_SEC
                if exit_decision is not None and exit_decision <= limit + 1e-12:
                    bucket = "already_exited"
                elif first is not None and first <= limit + 1e-12:
                    bucket = "already_ratcheted"
                else:
                    bucket = "open_ratchet0"
                ups = _upticks(book, index, signal_t) if bucket == "open_ratchet0" else None
                row["signal_index"] = index
                row["signal_uid"] = f"{day}|{session}|{row['symbol']}|{index}"
                row["bucket"] = bucket
                row["bid_upticks"] = ups
                baseline.append(row)
                population.append({"bucket": bucket, "bid_upticks": ups, "ratchets": int(row["ratchets"]), "date": day, "signal_uid": row["signal_uid"], "pnl_yen": float(row["pnl_yen"])})
            candidate.extend(row for row in cand["trades"] if row.get("pnl_yen") is not None)
            baseline_100.extend(row for row in lat_b["trades"] if row.get("pnl_yen") is not None)
            candidate_100.extend(row for row in lat_c["trades"] if row.get("pnl_yen") is not None)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} base={sum(1 for r in baseline if r['date']==day)} cand={sum(1 for r in candidate if r['date']==day)}", flush=True)
    return {
        "baseline": baseline,
        "candidate": candidate,
        "baseline_100": baseline_100,
        "candidate_100": candidate_100,
        "population": population,
        "uids": uids,
        "uids2": uids2,
        "observer_mismatch": observer_mismatch,
        "identity_miss": identity_miss,
        "dates": list(dates),
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
    }

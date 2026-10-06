"""Replay the frozen books and record precommitted causal descriptors. No new rule."""
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
from research.v2_fixed_support_heterogeneity_mechanism_audit_v1.descriptors import attach_audit_arrays, trade_descriptors
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.identity_sim import simulate_with_signal_index
from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import simulate_fixed_support


def _compact(row: dict[str, Any], signal_index: int) -> dict[str, Any]:
    return {
        "date": str(row["date"]),
        "session": str(row["session"]),
        "symbol": str(row["symbol"]),
        "signal_index": int(signal_index),
        "reason": str(row["reason"]),
        "pnl_yen": float(row["pnl_yen"]),
        "bps": float(row["bps"]),
        "entry_t": float(row["entry_t"]),
        "exit_t": float(row["exit_t"]),
        "exit_px": float(row["exit_px"]),
    }


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = _dates()
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
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
        traded = {"base": set(), "id": set(), "cand": set()}
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
                attach_audit_arrays(book, arrays)
                books[sym] = book
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded["base"])
            identified = simulate_with_signal_index(books, day=day, session=session, sess_end=end, traded_today=traded["id"])
            cand = simulate_fixed_support(books, day=day, session=session, sess_end=end, traded_today=traded["cand"])
            frozen_rows = [row for row in got["trades"] if row.get("pnl_yen") is not None]
            id_rows = identified["trades"]
            if len(frozen_rows) != len(id_rows):
                identity_miss += abs(len(frozen_rows) - len(id_rows))
            for row, ident in zip(frozen_rows, id_rows):
                if int(row["ratchets"]) != int(ident["ratchets"]) or str(row["reason"]) != str(ident["reason"]) or abs(float(row["entry_t"]) - float(ident["entry_t"])) > 1e-9 or abs(float(row["entry_px"]) - float(ident["entry_px"])) > 1e-6:
                    identity_miss += 1
                    continue
                described = trade_descriptors(books[str(row["symbol"])], int(ident["signal_index"]), float(row["entry_px"]))
                baseline.append({**_compact(row, int(ident["signal_index"])), **described})
            candidate.extend(_compact(row, int(row["signal_index"])) for row in cand["trades"] if row.get("pnl_yen") is not None)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} base={sum(1 for r in baseline if r['date']==day)} cand={sum(1 for r in candidate if r['date']==day)}", flush=True)
    return {"baseline": baseline, "candidate": candidate, "identity_miss": identity_miss, "dates": list(dates)}

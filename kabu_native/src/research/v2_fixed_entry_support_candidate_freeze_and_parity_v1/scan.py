"""Replay the research ablation and the candidate session engine on the same books."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book
from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import simulate_session as simulate_candidate
from research.event_time_impulse_v2_robustness_audit.latency import attach_quote_index
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import simulate_fixed_support


def _research_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": str(row["date"]),
        "session": str(row["session"]),
        "symbol": str(row["symbol"]),
        "signal_index": int(row["signal_index"]),
        "entry_t": float(row["entry_t"]),
        "exit_t": float(row["exit_t"]),
        "entry_px": float(row["entry_px"]),
        "exit_px": float(row["exit_px"]),
        "reason": str(row["reason"]),
        "pnl_yen": float(row["pnl_yen"]),
        "reconfirm_raises": int(row["ratchets"]),
        "initial_support": float(row["fixed_support"]),
        "final_support": float(row["fixed_support"]),
        "reentry": bool(row["reentry"]),
    }


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = _dates()
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    research: list[dict[str, Any]] = []
    implementation: list[dict[str, Any]] = []
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
        traded = {"research": set(), "impl": set()}
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
            ref = simulate_fixed_support(books, day=day, session=session, sess_end=end, traded_today=traded["research"])
            got = simulate_candidate(books, day=day, session=session, sess_end=end, traded_today=traded["impl"])
            research.extend(_research_row(row) for row in ref["trades"] if row.get("pnl_yen") is not None)
            implementation.extend(row for row in got["trades"] if row.get("pnl_yen") is not None)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(
            f"CAPTURE {day} research={sum(1 for r in research if r['date']==day)} impl={sum(1 for r in implementation if r['date']==day)}",
            flush=True,
        )
    return {
        "research": research,
        "implementation": implementation,
        "dates": list(dates),
        "universe_id": universe.universe_id,
        "universe_n": universe.symbol_count,
    }

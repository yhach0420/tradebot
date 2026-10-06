"""Load the exposed capture once. Baseline uses the frozen simulator."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_impulse_v2_robustness_audit.latency import LATENCIES_MS, attach_quote_index, simulate_latency
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    sector_of = {str(row["symbol"]): str(row.get("sector_id") or "") for row in mapping["rows"]}
    baseline: list[dict[str, Any]] = []
    latency: dict[int, list[dict[str, Any]]] = {ms: [] for ms in LATENCIES_MS}
    latency_counts: dict[int, dict[str, int]] = {ms: {"thesis_failed_before_fill": 0, "entry_pending_collision": 0, "exit_before_entry_fill": 0, "signal_while_entry_pending": 0, "entry_quote_miss": 0} for ms in LATENCIES_MS}
    push_records = 0
    for day in dates:
        capture = find_capture_dir(day)
        if capture is None or day >= "20260911":
            raise RuntimeError(f"refused_date:{day}")
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
            row = _board_row(pay, event_t)
            if event_t < am0 - 120.0 or event_t > pm1 + 2.0:
                continue
            bufs[sym].append(row)
            cum = row["cum_vol"]
            dvol = 0.0
            if cum is not None and cum == cum and cum >= 0:
                if last_cum[sym] is not None and cum >= float(last_cum[sym]):
                    dvol = float(cum - float(last_cum[sym]))
                last_cum[sym] = float(cum)
            if not row["continuous"]:
                continue
            if am0 <= event_t < am1:
                session = "AM"
            elif pm0 <= event_t < pm1:
                session = "PM"
            else:
                continue
            price = float(row["px"])
            finite_px = price == price and price > 0
            ask_vol, bid_vol = _classify(price, float(row["bid"]), float(row["ask"]), dvol)
            fresh = _quote_ok(row)
            cols[sym][session].add(
                t=event_t,
                px=price if finite_px else np.nan,
                vol=dvol,
                ask=ask_vol,
                bid=bid_vol,
                tick=1.0 if finite_px else 0.0,
                spread=(float(row["ask"]) - float(row["bid"])) if fresh else np.nan,
                ok=fresh,
                ask_px=float(row["ask"]) if fresh else np.nan,
            )
        day_full = 0
        traded_base: set[str] = set()
        traded_latency = {ms: set() for ms in LATENCIES_MS}
        for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
            books = {}
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
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded_base)
            for row in got["trades"]:
                row["sector"] = sector_of.get(row["symbol"], "")
            baseline.extend(got["trades"])
            for ms in LATENCIES_MS:
                stressed = simulate_latency(
                    books,
                    day=day,
                    session=session,
                    sess_end=end,
                    traded_today=traded_latency[ms],
                    latency_sec=ms / 1000.0,
                )
                for row in stressed["trades"]:
                    row["sector"] = sector_of.get(row["symbol"], "")
                latency[ms].extend(stressed["trades"])
                for key, value in stressed["counts"].items():
                    latency_counts[ms][key] += int(value)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} full={day_full} baseline_trades={sum(1 for row in baseline if row['date']==day)}", flush=True)
    return {
        "baseline": baseline,
        "latency": latency,
        "latency_counts": latency_counts,
        "dates": list(dates),
        "push_records": push_records,
        "min_capture_date_read": dates[0],
        "max_capture_date_read": dates[-1],
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
    }

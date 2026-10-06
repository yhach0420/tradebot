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
from research.v2_resistance_structure_audit_v1.anatomy import analyze
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18


def _maybe_chart(charts: list[dict[str, Any]], seen: set[tuple[str, str]], book: dict[str, Any], row: dict[str, Any]) -> None:
    if not row.get("ok"):
        return
    hold = float(row["exit_t"]) - float(row["entry_t"])
    pnl = float(row["pnl_yen"])
    ratchets = int(row["ratchets"])
    if ratchets == 0 and hold < 0.1 and pnl < 0:
        kind = "fail_100ms"
    elif ratchets == 1:
        kind = "ratchet_1"
    elif ratchets >= 2 and pnl > 0:
        kind = "ratchet_2_win"
    elif str(row["reason"]) == "IMPULSE_EXHAUSTED" and pnl > 0:
        kind = "exhausted_win"
    else:
        return
    if sum(1 for item in charts if item["kind"] == kind) >= 3:
        return
    key = (kind, str(row["date"]), str(row["symbol"]))
    if key in seen or any(item["kind"] == kind and item["date"] == row["date"] for item in charts):
        return
    seen.add(key)
    t0 = float(row["entry_t"]) - 60.0
    t1 = float(row["exit_t"]) + 5.0
    times = book["t"]
    left = int(np.searchsorted(times, t0, side="left"))
    right = int(np.searchsorted(times, t1, side="right"))
    bt = book["board_t"]
    bleft = int(np.searchsorted(bt, t0, side="left"))
    bright = int(np.searchsorted(bt, t1, side="right"))
    charts.append({
        "kind": kind,
        "symbol": row["symbol"],
        "date": row["date"],
        "session": row["session"],
        "entry_t": float(row["entry_t"]),
        "exit_t": float(row["exit_t"]),
        "entry_px": float(row["entry_px"]),
        "exit_px": float(row["exit_px"]),
        "reason": row["reason"],
        "pnl_yen": pnl,
        "ratchets": ratchets,
        "pre_break": row.get("pre_break"),
        "center": row.get("center"),
        "tick": row.get("tick"),
        "touch_class": row.get("touch_class"),
        "touch_n": row.get("touch_n"),
        "rejection_n": row.get("rejection_n"),
        "status": row.get("status"),
        "next_status": row.get("next_status"),
        "headroom_ticks": row.get("headroom_ticks"),
        "conversion": row.get("conversion"),
        "t": times[left:right].astype(float).tolist(),
        "px": book["px"][left:right].astype(float).tolist(),
        "bt": bt[bleft:bright].astype(float).tolist(),
        "bid": book["board_bid"][bleft:bright].astype(float).tolist(),
        "ask": book["board_ask"][bleft:bright].astype(float).tolist(),
    })


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
    charts: list[dict[str, Any]] = []
    seen_chart: set[tuple[str, str]] = set()
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
                book["board_t"] = board["t"]
                book["board_bid"] = board["bid"]
                book["board_ask"] = board["ask"]
                books[sym] = book
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded_base)
            for row in got["trades"]:
                if row.get("pnl_yen") is None:
                    continue
                row["sector"] = sector_of.get(row["symbol"], "")
                anatomy = analyze(books[row["symbol"]], row)
                row.update(anatomy)
                baseline.append(row)
                _maybe_chart(charts, seen_chart, books[row["symbol"]], row)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} full={day_full} baseline_trades={sum(1 for row in baseline if row['date']==day)}", flush=True)
    return {
        "baseline": baseline,
        "charts": charts,
        "dates": list(dates),
        "push_records": push_records,
        "min_capture_date_read": dates[0],
        "max_capture_date_read": dates[-1],
        "original": list(ORIGINAL18),
        "extension": list(EXTENSION17),
    }

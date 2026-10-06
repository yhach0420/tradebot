"""Reproduce the frozen ablation, then measure open risk and soft-exit integrity."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2 import MAX_CONCURRENT
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.event_time_impulse_v2_robustness_audit.latency import attach_quote_index, simulate_latency
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.v2_first_ratchet_transition_audit_v1.features import jpx_tick_size_yen
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.identity_sim import simulate_with_signal_index
from research.v2_genuine_resistance_gate_reconstruction_integrity_v1.observer import observe_from_signal
from research.v2_ratchet_state_transition_value_audit_v1.states import walk_position
from research.v2_support_ratchet_causal_ablation_v1.ablation_sim import simulate_fixed_support, simulate_fixed_support_latency
from research.v2_fixed_entry_support_candidate_audit_v1.risk import EquityTracker, extra_path, mark_session, verify_soft_exit

PERIODS = ("ORIGINAL18", "EXTENSION17", "FOLD1", "FOLD2", "FOLD3")


def _key(row: dict[str, Any]) -> tuple:
    return (str(row["date"]), str(row["session"]), str(row["symbol"]), int(row["signal_index"]))


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = _dates()
    folds = [dates[i * len(dates) // 3:(i + 1) * len(dates) // 3] for i in range(3)]
    period_dates = {
        "ORIGINAL18": set(ORIGINAL18),
        "EXTENSION17": set(EXTENSION17),
        "FOLD1": set(folds[0]),
        "FOLD2": set(folds[1]),
        "FOLD3": set(folds[2]),
    }
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    baseline: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    baseline_100: list[dict[str, Any]] = []
    candidate_100: list[dict[str, Any]] = []
    uids: list[str] = []
    uids2: list[str] = []
    observer_mismatch = identity_miss = walk_mismatch = soft_mismatch = 0
    soft_checked = 0
    transitions: list[dict[str, Any]] = []
    lost_rows: list[dict[str, Any]] = []
    trackers = {
        "base": EquityTracker(), "cand": EquityTracker(), "b100": EquityTracker(), "c100": EquityTracker(),
    }
    period_trackers = {
        name: {"base": EquityTracker(), "cand": EquityTracker()} for name in PERIODS
    }
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
        traded = {"base": set(), "id": set(), "c0": set(), "b100": set(), "c100": set()}
        active = [period_trackers[name] for name in PERIODS if day in period_dates[name]]
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
            cand = simulate_fixed_support(books, day=day, session=session, sess_end=end, traded_today=traded["c0"])
            lat_b = simulate_latency(books, day=day, session=session, sess_end=end, traded_today=traded["b100"], latency_sec=0.1, fill_model="asof")
            lat_c = simulate_fixed_support_latency(books, day=day, session=session, sess_end=end, traded_today=traded["c100"], latency_sec=0.1, fill_model="asof")
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
                if int(observe_from_signal(book, index)["ratchet_n"]) != int(row["ratchets"]):
                    observer_mismatch += 1
                walked = walk_position(book, index)
                if int(walked["ratchet_n"]) != int(row["ratchets"]):
                    walk_mismatch += 1
                gap = 0.0
                for item in walked["ratchets"]:
                    gap = max(gap, float(item["support"]) - float(walked["support0"]))
                level = float(walked["support0"])
                tick = jpx_tick_size_yen(level)
                row["signal_index"] = index
                row["signal_uid"] = f"{day}|{session}|{row['symbol']}|{index}"
                row["max_gap"] = gap
                row["max_gap_ticks"] = None if tick <= 0 else gap / tick
                row["max_gap_bps"] = None if level <= 0 else gap / level * 10000.0
            cand_rows = [row for row in cand["trades"] if row.get("pnl_yen") is not None]
            base_rows = [row for row in frozen_rows if "signal_index" in row]
            b100 = [row for row in lat_b["trades"] if row.get("pnl_yen") is not None]
            c100 = [row for row in lat_c["trades"] if row.get("pnl_yen") is not None]
            base_targets = [trackers["base"]] + [item["base"] for item in active]
            cand_targets = [trackers["cand"]] + [item["cand"] for item in active]
            mark_session(base_rows, books, base_targets)
            mark_session(cand_rows, books, cand_targets)
            mark_session(b100, books, [trackers["b100"]])
            mark_session(c100, books, [trackers["c100"]])
            for row in cand_rows:
                if str(row["reason"]) != "IMPULSE_EXHAUSTED":
                    continue
                soft_checked += 1
                checked = verify_soft_exit(books[str(row["symbol"])], int(row["signal_index"]), row, end)
                if not checked["ok"]:
                    soft_mismatch += 1
                    if soft_mismatch <= 8:
                        print(f"SOFT_MISMATCH {row['date']} {row['symbol']} {checked.get('why')}", flush=True)
            _audit_session(base_rows, cand_rows, books, transitions, lost_rows)
            baseline.extend(base_rows)
            candidate.extend(cand_rows)
            baseline_100.extend(b100)
            candidate_100.extend(c100)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} base={sum(1 for r in baseline if r['date']==day)} cand={sum(1 for r in candidate if r['date']==day)} soft_miss={soft_mismatch}", flush=True)
    return {
        "baseline": baseline, "candidate": candidate,
        "baseline_100": baseline_100, "candidate_100": candidate_100,
        "uids": uids, "uids2": uids2,
        "observer_mismatch": observer_mismatch, "identity_miss": identity_miss, "walk_mismatch": walk_mismatch,
        "soft_checked": soft_checked, "soft_mismatch": soft_mismatch,
        "transitions": transitions, "lost_rows": lost_rows,
        "trackers": {name: tr.snapshot() for name, tr in trackers.items()},
        "period_trackers": {name: {side: tr.snapshot() for side, tr in sides.items()} for name, sides in period_trackers.items()},
        "dates": list(dates), "original": list(ORIGINAL18), "extension": list(EXTENSION17),
        "folds": folds,
    }


def _audit_session(base_rows, cand_rows, books, transitions, lost_rows) -> None:
    cand_by = {_key(row): row for row in cand_rows}
    for row in base_rows:
        other = cand_by.get(_key(row))
        if other is None:
            lost_rows.append({
                "date": row["date"], "symbol": row["symbol"], "pnl_yen": float(row["pnl_yen"]),
                "reentry": bool(row["reentry"]), "reason": _block_reason(cand_rows, row),
                "exit_reason": str(row["reason"]),
            })
            continue
        later = float(other["exit_t"]) > float(row["exit_t"]) + 1e-9
        mae = mfe = None
        if later and str(row["reason"]) == "BREAK_SUPPORT_FAILURE":
            mae, mfe = extra_path(books[str(row["symbol"])], float(row["exit_t"]), float(other["exit_t"]), float(row["exit_px"]))
        transitions.append({
            "date": row["date"], "symbol": row["symbol"], "session": row["session"],
            "base_reason": str(row["reason"]), "cand_reason": str(other["reason"]),
            "base_pnl": float(row["pnl_yen"]), "cand_pnl": float(other["pnl_yen"]),
            "base_bps": float(row["bps"]), "cand_bps": float(other["bps"]),
            "base_exit": float(row["exit_px"]), "cand_exit": float(other["exit_px"]),
            "extra_hold": float(other["exit_t"]) - float(row["exit_t"]),
            "mae_after": mae, "mfe_after": mfe, "later": later,
        })


def _block_reason(cand_rows: list[dict[str, Any]], lost: dict[str, Any]) -> str:
    t = float(lost["entry_t"])
    index = int(lost["signal_index"])
    symbol = str(lost["symbol"])
    open_rows = []
    for row in cand_rows:
        started = float(row["entry_t"]) < t or (float(row["entry_t"]) == t and int(row["signal_index"]) < index)
        still = float(row["exit_t"]) > t
        if started and still:
            open_rows.append(row)
    if any(str(row["symbol"]) == symbol for row in open_rows):
        return "same_symbol"
    if len(open_rows) >= MAX_CONCURRENT:
        return "max_concurrent"
    return "other"

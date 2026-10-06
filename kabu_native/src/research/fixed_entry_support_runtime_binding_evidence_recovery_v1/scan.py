"""Replay verified registration windows through the reference gate and the runtime resolver."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book
from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import simulate_session
from research.event_time_impulse_v2_robustness_audit.latency import attach_quote_index
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.fixed_entry_support_runtime_binding_evidence_recovery_v1.evidence import (
    ROOT,
    bare,
    dynamic40_by_day,
    load_windows,
    registered_at,
)
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from small_paper.kabu_registration_authority import resolve_registered_probe_symbol


class _Gate:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, tuple[str, ...], str], tuple[bool, str]] = {}

    def decide(self, day: str, symbol: str, registered: frozenset[str]) -> tuple[bool, str]:
        key = (day, tuple(sorted(registered)), symbol)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = resolve_registered_probe_symbol(
            ROOT,
            day,
            actual_symbols=list(key[1]),
            proposed_symbol=symbol,
            push=None,
            write_audit=False,
        )
        reason = str(result.get("reason") or "")
        passed = bool(result.get("ok") and result.get("kabu_probe_symbol_registered"))
        if passed:
            label = "PASS"
        elif reason == "probe_symbol_not_in_actual_registered_set":
            label = "NOT_REGISTERED"
        elif reason == "actual_registered_exact50_required":
            label = "EXACT50_FAIL_CLOSED"
        else:
            label = reason or "FAIL_CLOSED"
        self._cache[key] = (passed, label)
        return self._cache[key]


def _with_signals(book: dict[str, Any], indices: list[int]) -> dict[str, Any]:
    copied = dict(book)
    copied["signals"] = np.asarray(indices, dtype=book["signals"].dtype)
    return copied


def _row(trade: dict[str, Any]) -> dict[str, Any]:
    return {key: trade[key] for key in (
        "date", "session", "symbol", "signal_index", "entry_t", "exit_t", "entry_px", "exit_px",
        "reason", "pnl_yen", "reconfirm_raises", "initial_support", "final_support", "reentry",
    )}


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    windows = load_windows()
    dynamic = dynamic40_by_day()
    gate = _Gate()
    dates = [day for day in _dates() if any(window["date"] == day for window in windows)]
    universe = load_research_observation_universe()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    reference: list[dict[str, Any]] = []
    implementation: list[dict[str, Any]] = []
    verified_signals = 0
    counts = {
        "eligibility": 0, "NOT_REGISTERED": 0, "Dynamic40": 0, "entry": 0, "support": 0,
        "reconfirm": 0, "exit": 0, "pnl": 0, "cap": 0, "same_symbol": 0, "occupancy": 0,
        "slot_release": 0, "reentry": 0, "session_close": 0,
    }
    first: Optional[dict[str, Any]] = None
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
        traded = {"reference": set(), "implementation": set()}
        day_dynamic = dynamic[day]
        for session, end in (("AM", am1), ("PM", pm1)):
            ref_books: dict[str, Any] = {}
            impl_books: dict[str, Any] = {}
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
                ref_idx: list[int] = []
                impl_idx: list[int] = []
                for raw in book["signals"]:
                    index = int(raw)
                    when = float(book["t"][index])
                    registered = registered_at(windows, day, when)
                    if registered is None:
                        continue
                    verified_signals += 1
                    in_dynamic = sym in day_dynamic
                    in_registered = sym in registered
                    ref_pass = in_dynamic and in_registered
                    ref_reason = "PASS" if ref_pass else ("NOT_DYNAMIC40" if not in_dynamic else "NOT_REGISTERED")
                    impl_pass, impl_reason = gate.decide(day, sym, registered)
                    if ref_pass != impl_pass:
                        counts["eligibility"] += 1
                        if "NOT_REGISTERED" in (ref_reason, impl_reason):
                            counts["NOT_REGISTERED"] += 1
                        if ref_reason == "NOT_DYNAMIC40" or (not in_dynamic and impl_pass):
                            counts["Dynamic40"] += 1
                        stamp = (day, session, when, sym, index)
                        if first is None or stamp < (first["date"], first["session"], float(first["timestamp"]), first["symbol"], int(first["signal_index"])):
                            first = {
                                "date": day,
                                "session": session,
                                "timestamp": when,
                                "symbol": sym,
                                "signal_uid": f"{day}|{session}|{sym}|{index}",
                                "signal_index": index,
                                "reference_state": ref_reason,
                                "runtime_state": impl_reason,
                                "root_cause_class": "RUNTIME_EXACT50_SET_EQUALITY" if impl_reason == "EXACT50_FAIL_CLOSED" and ref_pass else "ELIGIBILITY_DECISION",
                            }
                    if ref_pass:
                        ref_idx.append(index)
                    if impl_pass:
                        impl_idx.append(index)
                if ref_idx:
                    ref_books[sym] = _with_signals(book, ref_idx)
                if impl_idx:
                    impl_books[sym] = _with_signals(book, impl_idx)
            reference.extend(_row(row) for row in simulate_session(ref_books, day=day, session=session, sess_end=end, traded_today=traded["reference"])["trades"] if row.get("pnl_yen") is not None)
            implementation.extend(_row(row) for row in simulate_session(impl_books, day=day, session=session, sess_end=end, traded_today=traded["implementation"])["trades"] if row.get("pnl_yen") is not None)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}")
        print(f"CAPTURE {day} verified_signals={verified_signals} eligibility={counts['eligibility']}", flush=True)
    return {
        "windows": [{key: window[key] for key in ("date", "start", "end", "symbol_n", "source", "confirmations", "symbols")} for window in windows],
        "reference": reference,
        "implementation": implementation,
        "verified_signals": verified_signals,
        "signal_mismatches": counts,
        "first_divergence": first or "none",
        "dates": dates,
    }

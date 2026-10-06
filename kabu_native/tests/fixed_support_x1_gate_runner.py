"""One-event Gate B and Gate C parity. Existing capture only. No orders."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2.scan import _Col, _arrays, _dates, _full_signals
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book
from research.event_time_impulse_fixed_entry_support_candidate_v1.simulate import simulate_session
from research.event_time_volume_confirmed_impulse.features import evaluate
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.fixed_entry_support_runtime_binding_evidence_recovery_v1.evidence import (
    dynamic40_by_day,
    load_windows,
    registered_at,
)
from research.fixed_entry_support_runtime_exact50_reference_repair_v1.reference import reference_decision
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor

FIELDS = (
    "signal_index", "entry_t", "entry_px", "initial_support", "final_support",
    "reconfirm_raises", "exit_t", "exit_px", "reason", "pnl_yen", "reentry",
)


class Divergence(Exception):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("signal_uid"))
        self.payload = payload


def _pf(rows: list[dict[str, Any]]) -> Optional[float]:
    loss = sum(-float(row["pnl_yen"]) for row in rows if float(row["pnl_yen"]) < 0)
    gain = sum(float(row["pnl_yen"]) for row in rows if float(row["pnl_yen"]) > 0)
    return gain / loss if loss else None


def _close(left: Any, right: Any) -> bool:
    if isinstance(left, (float, int)) and isinstance(right, (float, int)) and not isinstance(left, bool):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def _mismatch(reference: list[dict[str, Any]], got: list[dict[str, Any]], *, gate: str, day: str, session: str) -> None:
    n = min(len(reference), len(got))
    for index in range(n):
        ref, exe = reference[index], got[index]
        diffs = [key for key in FIELDS if not _close(ref.get(key), exe.get(key))]
        if ref.get("symbol") != exe.get("symbol"):
            diffs.append("symbol")
        if diffs:
            raise Divergence(
                {
                    "gate": gate,
                    "date": day,
                    "session": session,
                    "symbol": exe.get("symbol"),
                    "signal_uid": exe.get("signal_uid"),
                    "reference_state": {key: ref.get(key) for key in ("symbol", *FIELDS)},
                    "executor_state": {key: exe.get(key) for key in ("symbol", *FIELDS)},
                    "diffs": diffs,
                }
            )
    if len(reference) != len(got):
        row = (reference if len(reference) > len(got) else got)[n]
        raise Divergence(
            {
                "gate": gate,
                "date": day,
                "session": session,
                "symbol": row.get("symbol"),
                "signal_uid": row.get("signal_uid"),
                "reference_n": len(reference),
                "executor_n": len(got),
                "diffs": ["trade_n"],
            }
        )


def _books(cols, bufs, symbols, session: str, start: float, admit) -> tuple[dict, dict]:
    full: dict[str, Any] = {}
    kept: dict[str, Any] = {}
    for sym in symbols:
        arrays, ask_px = _arrays(cols[sym][session])
        if int(arrays["t"].size) == 0:
            continue
        feat = evaluate(arrays["t"], arrays["px"], arrays["vol"], arrays["ask"], arrays["bid"], arrays["tick"], start)
        signals = _full_signals(feat, arrays)
        if int(signals.size) == 0:
            continue
        book = prepare_book(feat, arrays, bufs[sym].view(), signals, ask_px)
        full[sym] = book
        chosen = []
        for raw in signals:
            index = int(raw)
            ok, _reason = admit(sym, float(arrays["t"][index]))
            if ok:
                chosen.append(index)
        if chosen:
            copied = dict(book)
            copied["signals"] = np.asarray(chosen, dtype=signals.dtype)
            kept[sym] = copied
    return full, kept


def run(limit_days: Optional[int] = None) -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe

    dates = list(_dates())
    if limit_days is not None:
        dates = dates[: int(limit_days)]
    symbols = list(load_research_observation_universe().ordered_symbols)
    seen = set(symbols)
    windows = load_windows()
    dynamic = dynamic40_by_day()
    gate_b: list[dict[str, Any]] = []
    gate_c: list[dict[str, Any]] = []
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
        broad = FixedSupportX1SessionExecutor()
        exact = FixedSupportX1SessionExecutor()
        traded_b: set[str] = set()
        traded_c: set[str] = set()

        def admit(symbol: str, when: float, _day: str = day) -> tuple[bool, str]:
            registered = registered_at(windows, _day, when)
            if registered is None:
                return False, "NOT_REGISTERED"
            return reference_decision(symbol, dynamic[_day], registered)

        def begin(session: str, start: float, end: float) -> None:
            broad.start_session(day=day, session=session, sess_start=start, sess_end=end)
            exact.start_session(day=day, session=session, sess_start=start, sess_end=end, admission=admit)

        def finish(session: str, start: float, end: float) -> None:
            broad.on_session_boundary()
            exact.on_session_boundary()
            full, kept = _books(cols, bufs, symbols, session, start, admit)
            ref_b = simulate_session(full, day=day, session=session, sess_end=end, traded_today=traded_b)["trades"]
            ref_c = simulate_session(kept, day=day, session=session, sess_end=end, traded_today=traded_c)["trades"]
            got_b = [row for row in broad.trades if row["session"] == session]
            got_c = [row for row in exact.trades if row["session"] == session]
            _mismatch(ref_b, got_b, gate="B", day=day, session=session)
            _mismatch(ref_c, got_c, gate="C", day=day, session=session)

        begin("AM", am0, am1)
        phase = "AM"
        for rec in iter_push(Path(capture)):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if sym not in seen:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            event_t = float(et)
            if event_t < am0 - 120.0 or event_t > pm1 + 2.0:
                continue
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            row = _board_row(pay, event_t)
            if not row:
                continue
            if phase == "AM" and event_t >= am1:
                finish("AM", am0, am1)
                phase = "GAP"
            if phase == "GAP" and event_t >= pm0:
                begin("PM", pm0, pm1)
                phase = "PM"
            event = dict(row)
            event["symbol"] = sym
            event["t"] = event_t
            broad.on_market_event(event)
            exact.on_market_event(dict(event))
            bufs[sym].append(row)
            cum = row["cum_vol"]
            dvol = 0.0
            if cum is not None and cum == cum and cum >= 0:
                if last_cum[sym] is not None and float(cum) >= float(last_cum[sym]):
                    dvol = float(cum) - float(last_cum[sym])
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
            finite = price == price and price > 0
            ask_vol, bid_vol = _classify(price, float(row["bid"]), float(row["ask"]), dvol)
            fresh = _quote_ok(row)
            cols[sym][session].add(
                t=event_t,
                px=price if finite else np.nan,
                vol=dvol,
                ask=ask_vol,
                bid=bid_vol,
                tick=1.0 if finite else 0.0,
                spread=(float(row["ask"]) - float(row["bid"])) if fresh else np.nan,
                ok=fresh,
                ask_px=float(row["ask"]) if fresh else np.nan,
            )
        if phase == "AM":
            finish("AM", am0, am1)
        elif phase == "PM":
            finish("PM", pm0, pm1)
        gate_b.extend(broad.trades)
        gate_c.extend(exact.trades)
        print(f"CAPTURE {day} gate_b={len(gate_b)} gate_c={len(gate_c)}", flush=True)
    return {
        "gate_b_n": len(gate_b),
        "gate_b_pnl": float(sum(float(row["pnl_yen"]) for row in gate_b)),
        "gate_b_pf": _pf(gate_b),
        "gate_c_n": len(gate_c),
        "gate_c_pnl": float(sum(float(row["pnl_yen"]) for row in gate_c)),
        "gate_c_pf": _pf(gate_c),
        "dates": dates,
        "divergence": None,
    }


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        result = run(limit)
    except Divergence as exc:
        print("FIRST_DIVERGENCE", exc.payload, flush=True)
        raise SystemExit(2)
    print("GATE_RESULT", result, flush=True)

"""One pass over the exposed capture. The parent FULL predicate is not edited."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_impulse_complete_strategy_v2 import CAPTURE_LAST
from research.event_time_volume_confirmed_impulse import FRESH_SEC, MIN_QTY
from research.event_time_volume_confirmed_impulse.features import evaluate, first_events, spread_not_worse
from research.event_time_volume_confirmed_impulse.scan import _classify, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL
import research.stock_specific_sequential_setup.episodes  # noqa: F401
from research.event_time_impulse_complete_strategy_v2.simulate import prepare_book, simulate_session
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18


class _Col:
    __slots__ = ("t", "px", "vol", "ask", "bid", "tick", "spread", "ok", "ask_px")

    def __init__(self) -> None:
        self.t: list[float] = []
        self.px: list[float] = []
        self.vol: list[float] = []
        self.ask: list[float] = []
        self.bid: list[float] = []
        self.tick: list[float] = []
        self.spread: list[float] = []
        self.ok: list[bool] = []
        self.ask_px: list[float] = []

    def add(self, **item: Any) -> None:
        self.t.append(float(item["t"]))
        self.px.append(float(item["px"]))
        self.vol.append(float(item["vol"]))
        self.ask.append(float(item["ask"]))
        self.bid.append(float(item["bid"]))
        self.tick.append(float(item["tick"]))
        self.spread.append(float(item["spread"]))
        self.ok.append(bool(item["ok"]))
        self.ask_px.append(float(item["ask_px"]))


def _dates() -> tuple[str, ...]:
    dates = tuple(sorted(set(ORIGINAL18 + EXTENSION17)))
    if dates[0] != "20260722" or dates[-1] != CAPTURE_LAST or any(day >= "20260911" for day in dates):
        raise RuntimeError("capture_date_outside_exposed_window")
    return dates


def _arrays(col: _Col) -> tuple[dict[str, np.ndarray], np.ndarray]:
    order = np.argsort(np.asarray(col.t, dtype=float), kind="mergesort")
    raw_t = np.asarray(col.t, dtype=float)
    return {
        "t": raw_t[order],
        "px": np.asarray(col.px, dtype=float)[order],
        "vol": np.asarray(col.vol, dtype=float)[order],
        "ask": np.asarray(col.ask, dtype=float)[order],
        "bid": np.asarray(col.bid, dtype=float)[order],
        "tick": np.asarray(col.tick, dtype=float)[order],
        "spread": np.asarray(col.spread, dtype=float)[order],
        "ok": np.asarray(col.ok, dtype=bool)[order],
    }, np.asarray(col.ask_px, dtype=float)[order]


def _full_signals(feat: dict[str, Any], arrays: dict[str, np.ndarray]) -> np.ndarray:
    pre = (
        feat["vol_accel_10"]
        & feat["vol_accel_30"]
        & feat["buy"]
        & (feat["classified10"] > 0)
        & feat["tick_accel_10"]
        & feat["price_break"]
    )
    full = np.array(pre, dtype=bool)
    for index in np.flatnonzero(pre).tolist():
        full[index] = spread_not_worse(arrays["t"], arrays["spread"], arrays["ok"], int(index))
    return first_events(full, arrays["px"], feat["pre_high"])


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = list(universe.ordered_symbols)
    seen = set(symbols)
    original = set(ORIGINAL18)
    trades: list[dict[str, Any]] = []
    totals = {key: 0 for key in ("full", "valid", "unavailable", "raw_entry", "same_symbol", "occupancy", "cap", "first_entry", "reentry", "ratchet")}
    push_records = 0
    max_date = None
    for day in dates:
        if day > CAPTURE_LAST or day >= "20260911":
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
        traded: set[str] = set()
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
                books[sym] = prepare_book(feat, arrays, bufs[sym].view(), signals, ask_px)
            got = simulate_session(books, day=day, session=session, sess_end=end, traded_today=traded)
            trades.extend(got["trades"])
            for key, value in got["counts"].items():
                totals[key] += int(value)
        if day_full != int(EXPECTED_DAY_FULL[day]):
            raise RuntimeError(f"signal_identity_mismatch:{day}:{day_full}:{EXPECTED_DAY_FULL[day]}")
        max_date = day
        print(f"CAPTURE {day} full={day_full} trades={sum(1 for row in trades if row['date'] == day)}", flush=True)
    return {
        "trades": trades,
        "counts": totals,
        "push_records": push_records,
        "max_capture_date_read": max_date,
        "min_capture_date_read": dates[0],
        "dates": list(dates),
        "original": sorted(ORIGINAL18),
        "extension": sorted(EXTENSION17),
        "universe_sha256": universe.source_sha256,
        "sector_mapping_sha256": mapping.get("sector_mapping_sha256"),
        "prospective_rows_read": 0,
        "fv_rows_read": 0,
        "lineage_of": {day: ("ORIGINAL18" if day in original else "EXTENSION17") for day in dates},
    }

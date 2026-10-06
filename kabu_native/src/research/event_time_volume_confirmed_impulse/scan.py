"""Read the exposed capture once and apply the frozen event-time predicates."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_volume_confirmed_impulse import CAPTURE_LAST, FRESH_SEC, HORIZONS, MIN_QTY
from research.event_time_volume_confirmed_impulse.features import evaluate, first_events, spread_not_worse
import research.stock_specific_sequential_setup.episodes  # noqa: F401  # initialize before universe
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.symbol_setup_failure_evidence_gap.scan import _first_quote, _joint_mask, _next_ok


def _dates() -> tuple[str, ...]:
    dates = tuple(sorted(set(ORIGINAL18 + EXTENSION17)))
    if dates[0] < "20260722" or dates[-1] > CAPTURE_LAST or any(day >= "20260911" for day in dates):
        raise RuntimeError("capture_date_outside_exposed_window")
    return dates


def _quote_ok(row: dict[str, Any]) -> bool:
    bid = float(row["bid"])
    ask = float(row["ask"])
    fresh = float(row["fresh_sec"])
    bid_qty = float(row["bid_qty"])
    ask_qty = float(row["ask_qty"])
    return bool(
        row["executable"]
        and not row["special"]
        and fresh == fresh
        and fresh <= FRESH_SEC + 1e-12
        and bid == bid
        and ask == ask
        and bid > 0
        and ask >= bid
        and bid_qty == bid_qty
        and ask_qty == ask_qty
        and bid_qty >= MIN_QTY - 1e-12
        and ask_qty >= MIN_QTY - 1e-12
    )


def _classify(price: float, bid: float, ask: float, dvol: float) -> tuple[float, float]:
    if dvol <= 0 or not (price == price and price > 0):
        return 0.0, 0.0
    if ask == ask and ask > 0 and price + 1e-12 >= ask:
        return dvol, 0.0
    if bid == bid and bid > 0 and price - 1e-12 <= bid:
        return 0.0, dvol
    return 0.0, 0.0


class _Col:
    __slots__ = ("t", "px", "vol", "ask", "bid", "tick", "spread", "ok")

    def __init__(self) -> None:
        self.t: list[float] = []
        self.px: list[float] = []
        self.vol: list[float] = []
        self.ask: list[float] = []
        self.bid: list[float] = []
        self.tick: list[float] = []
        self.spread: list[float] = []
        self.ok: list[bool] = []

    def add(self, **item: Any) -> None:
        self.t.append(float(item["t"]))
        self.px.append(float(item["px"]))
        self.vol.append(float(item["vol"]))
        self.ask.append(float(item["ask"]))
        self.bid.append(float(item["bid"]))
        self.tick.append(float(item["tick"]))
        self.spread.append(float(item["spread"]))
        self.ok.append(bool(item["ok"]))


def _as_arrays(col: _Col) -> dict[str, np.ndarray]:
    order = np.argsort(np.asarray(col.t, dtype=float), kind="mergesort")
    raw_t = np.asarray(col.t, dtype=float)
    inversions = int(np.sum(raw_t[1:] + 1e-9 < raw_t[:-1])) if raw_t.size else 0
    return {
        "t": raw_t[order],
        "px": np.asarray(col.px, dtype=float)[order],
        "vol": np.asarray(col.vol, dtype=float)[order],
        "ask": np.asarray(col.ask, dtype=float)[order],
        "bid": np.asarray(col.bid, dtype=float)[order],
        "tick": np.asarray(col.tick, dtype=float)[order],
        "spread": np.asarray(col.spread, dtype=float)[order],
        "ok": np.asarray(col.ok, dtype=bool)[order],
        "inversions": inversions,
    }


def _mark(board: dict[str, np.ndarray], nxt: np.ndarray, event_t: float, sess_end: float) -> tuple[dict[str, Optional[float]], int]:
    out: dict[str, Optional[float]] = {}
    q0 = _first_quote(board, nxt, event_t, sess_end)
    fails = 0
    if q0 is None or not (float(q0["ask"]) > 0):
        for horizon in HORIZONS:
            out[f"raw_{horizon}"] = None
            out[f"bid_{horizon}"] = None
            out[f"atb_{horizon}"] = None
        return out, fails
    bid0 = float(q0["bid"])
    ask0 = float(q0["ask"])
    mid0 = (bid0 + ask0) / 2.0
    for horizon in HORIZONS:
        qh = _first_quote(board, nxt, float(q0["t"]) + float(horizon), sess_end)
        if qh is None:
            out[f"raw_{horizon}"] = None
            out[f"bid_{horizon}"] = None
            out[f"atb_{horizon}"] = None
            continue
        bidh = float(qh["bid"])
        askh = float(qh["ask"])
        midh = (bidh + askh) / 2.0
        raw = midh - mid0
        entry = ask0 - mid0
        exit_ = midh - bidh
        atb = bidh - ask0
        if abs(atb - (raw - entry - exit_)) > 1e-6:
            fails += 1
        out[f"raw_{horizon}"] = raw / ask0 * 10000.0
        out[f"bid_{horizon}"] = (bidh - bid0) / ask0 * 10000.0
        out[f"atb_{horizon}"] = atb / ask0 * 10000.0
    return out, fails


def _population_rows(
    name: str,
    indices: np.ndarray,
    feat: dict[str, Any],
    arrays: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    nxt: np.ndarray,
    sess_end: float,
    meta: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    rows = []
    fails = 0
    px = arrays["px"]
    for index in indices.tolist():
        vol10 = float(feat["vol10"][index])
        classified = float(feat["classified10"][index])
        packed, bad = _mark(board, nxt, float(arrays["t"][index]), sess_end)
        fails += bad
        row = {
            "population": name,
            "date": meta["date"],
            "symbol": meta["symbol"],
            "sector": meta["sector"],
            "session": meta["session"],
            "lineage": meta["lineage"],
            "fold": meta["fold"],
            "classified_fraction_10": None if vol10 <= 0 else classified / vol10,
        }
        row.update(packed)
        rows.append(row)
    return rows, fails


def _session(
    arrays: dict[str, np.ndarray],
    session_start: float,
    sess_end: float,
    board: dict[str, np.ndarray],
    nxt: np.ndarray,
    meta: dict[str, Any],
    coverage: dict[str, int],
) -> tuple[list[dict[str, Any]], int]:
    if int(arrays["t"].size) == 0:
        return [], 0
    feat = evaluate(
        arrays["t"],
        arrays["px"],
        arrays["vol"],
        arrays["ask"],
        arrays["bid"],
        arrays["tick"],
        session_start,
    )
    ready = feat["ready10"] & (feat["vol10"] > 0)
    coverage["volume_windows"] += int(np.sum(ready))
    coverage["classified_windows"] += int(np.sum(ready & (feat["classified10"] > 0)))
    pre_spread = (
        feat["vol_accel_10"]
        & feat["vol_accel_30"]
        & feat["buy"]
        & (feat["classified10"] > 0)
        & feat["tick_accel_10"]
        & feat["price_break"]
    )
    full = np.array(pre_spread, dtype=bool)
    spread = arrays["spread"]
    ok = arrays["ok"]
    t = arrays["t"]
    for index in np.flatnonzero(pre_spread).tolist():
        full[index] = spread_not_worse(t, spread, ok, int(index))
    no_buy = feat["price_break"] & feat["vol_accel_10"] & feat["vol_accel_30"] & ~feat["buy"]
    selected = {
        "FULL": first_events(full, arrays["px"], feat["pre_high"]),
        "PRICE_BREAK_ONLY": first_events(feat["price_break"], arrays["px"], feat["pre_high"]),
        "VOLUME_BREAK_NO_BUY": first_events(no_buy, arrays["px"], feat["pre_high"]),
    }
    rows: list[dict[str, Any]] = []
    fails = 0
    for name, indices in selected.items():
        got, bad = _population_rows(name, indices, feat, arrays, board, nxt, sess_end, meta)
        rows.extend(got)
        fails += bad
    return rows, fails


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    dates = _dates()
    universe = load_research_observation_universe()
    mapping = bind_sector_mapping()
    symbols = set(universe.ordered_symbols)
    sector_of = {str(row["symbol"]): str(row["sector_id"] or "UNMAPPED") for row in mapping["rows"]}
    original = set(ORIGINAL18)
    fold_of: dict[str, int] = {}
    cuts = [int(round(i * len(dates) / 3)) for i in range(4)]
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for day in dates[lo:hi]:
            fold_of[day] = fold
    rows: list[dict[str, Any]] = []
    coverage = {"volume_windows": 0, "classified_windows": 0, "push_records": 0, "time_inversions": 0, "parity_fails": 0}
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
        lineage = "ORIGINAL18" if day in original else "EXTENSION17"
        for rec in iter_push(Path(capture)):
            coverage["push_records"] += 1
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if sym not in symbols:
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
                start_ok = True
            elif pm0 <= event_t < pm1:
                session = "PM"
                start_ok = True
            else:
                start_ok = False
            if not start_ok:
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
            )
        day_n = {"FULL": 0, "PRICE_BREAK_ONLY": 0, "VOLUME_BREAK_NO_BUY": 0}
        for sym in symbols:
            board = bufs[sym].view()
            nxt = _next_ok(_joint_mask(board))
            meta = {"date": day, "symbol": sym, "sector": sector_of.get(sym, "UNMAPPED"), "lineage": lineage, "fold": fold_of[day]}
            for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
                arrays = _as_arrays(cols[sym][session])
                coverage["time_inversions"] += int(arrays.pop("inversions"))
                meta["session"] = session
                got, fails = _session(arrays, start, end, board, nxt, meta, coverage)
                coverage["parity_fails"] += fails
                rows.extend(got)
                for item in got:
                    day_n[item["population"]] += 1
        max_date = day
        print(
            f"CAPTURE {day} full={day_n['FULL']} break={day_n['PRICE_BREAK_ONLY']} nobuy={day_n['VOLUME_BREAK_NO_BUY']}",
            flush=True,
        )
    return {
        "rows": rows,
        "coverage": coverage,
        "max_capture_date_read": max_date,
        "min_capture_date_read": dates[0],
        "dates": dates,
        "universe_n": len(symbols),
        "universe_sha256": universe.source_sha256,
        "sector_mapping_sha256": mapping["sector_mapping_sha256"],
        "prospective_rows_read": 0,
        "fv_rows_read": 0,
    }

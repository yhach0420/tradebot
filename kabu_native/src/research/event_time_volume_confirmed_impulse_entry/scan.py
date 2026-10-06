"""Replay the frozen FULL signal and apply the passive-bid entry. No new predicate."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness import AM_END, AM_START, PM_END, PM_START
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, find_capture_dir, iter_push, record_event_stamp
from research.event_time_volume_confirmed_impulse.features import evaluate, first_events, spread_not_worse, window_sum
from research.event_time_volume_confirmed_impulse.scan import _classify, _dates, _mark, _quote_ok
from research.event_time_volume_confirmed_impulse_entry import EXPECTED_DAY_FULL, HORIZONS, PARENT_FULL_N
from research.event_time_volume_confirmed_impulse_entry.execution import bid_next, future_bid, passive_bid_fill, path_excursions
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18
from research.symbol_setup_failure_evidence_gap.scan import _joint_mask, _next_ok

import research.stock_specific_sequential_setup.episodes  # noqa: F401


class _Col:
    __slots__ = ("t", "px", "vol", "ask", "bid", "tick", "spread", "ok", "bid_px", "ask_px")

    def __init__(self) -> None:
        self.t: list[float] = []
        self.px: list[float] = []
        self.vol: list[float] = []
        self.ask: list[float] = []
        self.bid: list[float] = []
        self.tick: list[float] = []
        self.spread: list[float] = []
        self.ok: list[bool] = []
        self.bid_px: list[float] = []
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
        self.bid_px.append(float(item["bid_px"]))
        self.ask_px.append(float(item["ask_px"]))


def _arrays(col: _Col) -> dict[str, np.ndarray]:
    order = np.argsort(np.asarray(col.t, dtype=float), kind="mergesort")
    raw_t = np.asarray(col.t, dtype=float)

    def take(values: list[Any], dtype: Any) -> np.ndarray:
        return np.asarray(values, dtype=dtype)[order]

    return {
        "t": raw_t[order],
        "px": take(col.px, float),
        "vol": take(col.vol, float),
        "ask": take(col.ask, float),
        "bid": take(col.bid, float),
        "tick": take(col.tick, float),
        "spread": take(col.spread, float),
        "ok": take(col.ok, bool),
        "bid_px": take(col.bid_px, float),
        "ask_px": take(col.ask_px, float),
    }


def _prefix(values: np.ndarray) -> np.ndarray:
    out = np.zeros(int(values.size) + 1, dtype=float)
    np.cumsum(values, out=out[1:])
    return out


def _signals(
    arrays: dict[str, np.ndarray],
    session_start: float,
    sess_end: float,
    board: dict[str, np.ndarray],
    nxt: np.ndarray,
    bid_nxt: np.ndarray,
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    if int(arrays["t"].size) == 0:
        return []
    feat = evaluate(
        arrays["t"],
        arrays["px"],
        arrays["vol"],
        arrays["ask"],
        arrays["bid"],
        arrays["tick"],
        session_start,
    )
    pre_spread = (
        feat["vol_accel_10"]
        & feat["vol_accel_30"]
        & feat["buy"]
        & (feat["classified10"] > 0)
        & feat["tick_accel_10"]
        & feat["price_break"]
    )
    full = np.array(pre_spread, dtype=bool)
    for index in np.flatnonzero(pre_spread).tolist():
        full[index] = spread_not_worse(arrays["t"], arrays["spread"], arrays["ok"], int(index))
    chosen = first_events(full, arrays["px"], feat["pre_high"])
    vol30 = window_sum(_prefix(arrays["vol"]), arrays["t"], 30.0, 0.0)
    ticks10 = window_sum(_prefix(arrays["tick"]), arrays["t"], 10.0, 0.0)
    rows = []
    for index in chosen.tolist():
        signal_t = float(arrays["t"][index])
        bid0 = float(arrays["bid_px"][index])
        ask0 = float(arrays["ask_px"][index])
        trigger = float(arrays["px"][index])
        vol10 = float(feat["vol10"][index])
        classified = float(feat["classified10"][index])
        marked, _bad = _mark(board, nxt, signal_t, sess_end)
        fill_t = passive_bid_fill(board, signal_t=signal_t, limit_price=bid0, sess_end=sess_end)
        filled = fill_t is not None
        row: dict[str, Any] = {
            "date": meta["date"],
            "symbol": meta["symbol"],
            "sector": meta["sector"],
            "session": meta["session"],
            "lineage": meta["lineage"],
            "fold": meta["fold"],
            "signal_t": signal_t,
            "break_level": float(feat["pre_high"][index]),
            "trigger_price": trigger,
            "bid0": bid0,
            "ask0": ask0,
            "spread0": ask0 - bid0,
            "volume_10s": vol10,
            "volume_30s": float(vol30[index]),
            "ask_vol_10s": float(feat["ask10"][index]),
            "bid_vol_10s": float(feat["bid10"][index]),
            "tick_count_10s": float(ticks10[index]),
            "classified_volume_fraction": None if vol10 <= 0 else classified / vol10,
            "signal_bid_180": marked.get("bid_180"),
            "signal_raw_180": marked.get("raw_180"),
            "signal_atb_180": marked.get("atb_180"),
            "filled": filled,
            "fill_t": fill_t,
            "fill_price": bid0 if filled else None,
            "fill_latency_sec": None if fill_t is None else float(fill_t) - signal_t,
        }
        if filled and fill_t is not None:
            mid0 = (bid0 + ask0) / 2.0
            row["signal_bid_to_fill_bps"] = 0.0
            row["signal_mid_to_fill_bps"] = None if mid0 <= 0 else (bid0 - mid0) / mid0 * 10000.0
            mfe, mae = path_excursions(board, signal_t, float(fill_t), trigger)
            row["pre_fill_mfe_bps"] = mfe
            row["pre_fill_mae_bps"] = mae
            for horizon in HORIZONS:
                future = future_bid(board, bid_nxt, float(fill_t) + float(horizon), sess_end)
                row[f"fill_bid_{horizon}"] = None if future is None else (future - bid0) / bid0 * 10000.0
        else:
            row["signal_bid_to_fill_bps"] = None
            row["signal_mid_to_fill_bps"] = None
            row["pre_fill_mfe_bps"] = None
            row["pre_fill_mae_bps"] = None
            for horizon in HORIZONS:
                row[f"fill_bid_{horizon}"] = None
        rows.append(row)
    return rows


def scan() -> dict[str, Any]:
    from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
    from research.causal_driver_pb1.phase2_precommit.sector_map import bind_sector_mapping

    if sum(EXPECTED_DAY_FULL.values()) != PARENT_FULL_N:
        raise RuntimeError("parent_day_count_mismatch")
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
    max_date = None
    push_records = 0
    for day in dates:
        if day > "20260910" or day >= "20260911":
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
            push_records += 1
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
                bid_px=float(row["bid"]) if fresh else np.nan,
                ask_px=float(row["ask"]) if fresh else np.nan,
            )
        day_rows: list[dict[str, Any]] = []
        for sym in symbols:
            board = bufs[sym].view()
            nxt = _next_ok(_joint_mask(board))
            bids = bid_next(board)
            meta = {
                "date": day,
                "symbol": sym,
                "sector": sector_of.get(sym, "UNMAPPED"),
                "lineage": lineage,
                "fold": fold_of[day],
            }
            for session, start, end in (("AM", am0, am1), ("PM", pm0, pm1)):
                meta["session"] = session
                day_rows.extend(_signals(_arrays(cols[sym][session]), start, end, board, nxt, bids, meta))
        expected = EXPECTED_DAY_FULL.get(day)
        if expected is None or len(day_rows) != expected:
            return {
                "identity_ok": False,
                "mismatch_day": day,
                "got_n": len(day_rows),
                "expected_n": expected,
                "rows": rows,
                "max_capture_date_read": day,
                "min_capture_date_read": dates[0],
                "push_records": push_records,
                "universe_sha256": universe.source_sha256,
                "sector_mapping_sha256": mapping["sector_mapping_sha256"],
            }
        filled = sum(1 for row in day_rows if row["filled"])
        rows.extend(day_rows)
        max_date = day
        print(f"CAPTURE {day} full={len(day_rows)} filled={filled} expired={len(day_rows) - filled}", flush=True)
    return {
        "identity_ok": True,
        "rows": rows,
        "max_capture_date_read": max_date,
        "min_capture_date_read": dates[0],
        "push_records": push_records,
        "universe_n": len(symbols),
        "universe_sha256": universe.source_sha256,
        "sector_mapping_sha256": mapping["sector_mapping_sha256"],
        "prospective_rows_read": 0,
    }

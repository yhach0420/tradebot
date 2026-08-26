"""One-day T1+C1 event generation. No ENTRY/FILL/EXIT/PnL."""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from research.dynamic_anchor_p2_0b import (
    CHECKPOINT_STALE,
    CONFIRMATION_NOT_EVALUABLE,
    CONFIRMED,
    MAX_CHECKPOINT_AGE_SEC,
    REJECTED,
    SESSION_INCOMPLETE,
)
from research.dynamic_anchor_p2_0b.contract import (
    SymbolMachine,
    checkpoint_epochs,
    ols_log_trend_slope,
    t1_raw,
)
from research.dynamic_anchor_p2_1 import (
    CAPTURE_BOUNDARY_INCOMPLETE,
    CHECKPOINT_MISSING,
    INVALID_PRICE,
    OTHER,
    P0_MISSING,
)
from research.e1_x14_board_independent_signal.features import (
    attach_path_volume_features,
    attach_relative_strength,
)
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid


def first_event_after_np(t1: float, times: np.ndarray) -> Optional[float]:
    if times.size == 0:
        return None
    i = int(np.searchsorted(times, float(t1), side="right"))
    if i >= times.size:
        return None
    t = float(times[i])
    if t <= float(t1) + 1e-12:
        return None
    return t


def confirm_c1(
    *,
    symbol: str,
    t0: float,
    t1: float,
    times: np.ndarray,
    prices: np.ndarray,
) -> dict[str, Any]:
    marks = checkpoint_epochs(t0)
    out_prices: list[float] = []
    max_src = None
    leak = 0
    for i, c in enumerate(marks):
        if times.size == 0:
            reason = P0_MISSING if i == 0 else CHECKPOINT_MISSING
            return _ne(symbol, t0, t1, reason, i, leak)
        j = int(np.searchsorted(times, min(float(c), float(t1)), side="right") - 1)
        if j < 0:
            reason = P0_MISSING if i == 0 else CHECKPOINT_MISSING
            return _ne(symbol, t0, t1, reason, i, leak)
        et = float(times[j])
        if et > float(c) + 1e-12 or et > float(t1) + 1e-12:
            leak += 1
            return _ne(symbol, t0, t1, "CHECKPOINT_FUTURE_LEAK", i, leak)
        px = float(prices[j])
        if not math.isfinite(px) or px <= 0:
            return _ne(symbol, t0, t1, INVALID_PRICE, i, leak)
        age = float(c) - et
        if age > MAX_CHECKPOINT_AGE_SEC + 1e-12:
            return _ne(symbol, t0, t1, CHECKPOINT_STALE, i, leak)
        out_prices.append(px)
        max_src = et if max_src is None else max(max_src, et)
    slope = ols_log_trend_slope(out_prices)
    p0, p10 = out_prices[0], out_prices[-1]
    endpoint = p10 > p0
    ok = bool(slope > 0.0 and endpoint)
    return {
        "status": CONFIRMED if ok else REJECTED,
        "reason": None if ok else "SLOPE_OR_ENDPOINT_FAIL",
        "symbol": symbol,
        "t0": float(t0),
        "t1": float(t1),
        "trend_slope": slope,
        "p0": p0,
        "p10": p10,
        "endpoint_return": (p10 / p0) - 1.0,
        "p10_gt_p0": endpoint,
        "fail_checkpoint": None,
        "checkpoint_future_leak": leak,
        "max_checkpoint_source_time": max_src,
    }


def _ne(symbol, t0, t1, reason, fail_i, leak) -> dict[str, Any]:
    return {
        "status": CONFIRMATION_NOT_EVALUABLE,
        "reason": reason or OTHER,
        "symbol": symbol,
        "t0": float(t0),
        "t1": float(t1),
        "trend_slope": None,
        "p0": None,
        "p10": None,
        "endpoint_return": None,
        "p10_gt_p0": None,
        "fail_checkpoint": int(fail_i),
        "checkpoint_future_leak": leak,
        "max_checkpoint_source_time": None,
    }


def build_day_features(day: str, ticks_by: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    grids: dict[str, list[dict[str, Any]]] = {}
    for sym, ticks in ticks_by.items():
        rows = build_symbol_day_grid(day, sym, ticks, f"capture_{day}")
        times = np.asarray([t["t"] for t in ticks], dtype=float) if ticks else np.zeros(0)
        for r in rows:
            i = int(r.get("_tick_idx") or -1)
            r["source_tick_t"] = float(times[i]) if i >= 0 and i < times.size else None
        grids[sym] = attach_path_volume_features(rows, ticks)
    attach_relative_strength(grids)
    return grids


def xs_audit_rows(grids: dict[str, list[dict[str, Any]]], *, max_ok: int = 40) -> tuple[list[dict[str, Any]], int]:
    """Sample + count future-leak on volume_percentile peers."""
    by_g: dict[float, list[dict[str, Any]]] = {}
    for rows in grids.values():
        for r in rows:
            if r.get("volume_percentile_60s") is None:
                continue
            if r.get("relative_status") != "OK":
                continue
            by_g.setdefault(float(r["grid_epoch"]), []).append(r)
    leaks = 0
    samples = []
    for g, rows in sorted(by_g.items()):
        srcs = [float(r["source_tick_t"]) for r in rows if r.get("source_tick_t") is not None]
        mx = max(srcs) if srcs else None
        leak = bool(mx is not None and mx > g + 1e-12)
        if leak:
            leaks += 1
        if leak or len(samples) < max_ok:
            # pick one symbol as focal (first by name)
            rows_s = sorted(rows, key=lambda x: str(x["symbol"]))
            foc = rows_s[len(rows_s) // 2]
            samples.append({
                "date": foc.get("date"),
                "grid_time": foc.get("grid_time"),
                "grid_epoch": g,
                "symbol": foc.get("symbol"),
                "feature_value": foc.get("volume_percentile_60s"),
                "peer_count": len(rows),
                "max_peer_source_time": mx,
                "threshold": 0.6486486486486487,
                "raw": t1_raw(foc),
                "max_peer_source_time_le_grid": (not leak),
            })
    return samples, leaks


def _price_arrays(ticks: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    if not ticks:
        return np.zeros(0), np.zeros(0)
    t = np.asarray([x["t"] for x in ticks], dtype=float)
    p = np.asarray([x["price"] for x in ticks], dtype=float)
    return t, p


def walk_symbol_session(
    *,
    day: str,
    symbol: str,
    session: str,
    rows: list[dict[str, Any]],
    times: np.ndarray,
    prices: np.ndarray,
    global_times: np.ndarray,
    last_capture_t: Optional[float],
    capture_class: str,
) -> dict[str, Any]:
    sm = SymbolMachine(symbol)
    rows = sorted(rows, key=lambda r: float(r["grid_epoch"]))
    triggers: list[dict[str, Any]] = []
    confirms: list[dict[str, Any]] = []
    persist_refire = 0
    grid_n = 0
    eval_n = 0
    raw_true_n = 0

    def _evaluable(r: dict[str, Any]) -> bool:
        if r.get("feature_status") != "OK":
            return False
        if r.get("relative_status") != "OK":
            return False
        try:
            n = int(r.get("rs_universe_n") or 0)
        except (TypeError, ValueError):
            return False
        if n < 20:
            return False
        v = r.get("volume_percentile_60s")
        try:
            x = float(v)
        except (TypeError, ValueError):
            return False
        return math.isfinite(x)

    def finish_active() -> None:
        if sm.active is None:
            return
        anc = sm.active
        t1 = float(anc.t1)
        if last_capture_t is not None and float(last_capture_t) + 1e-12 < t1:
            sm.close_active(CAPTURE_BOUNDARY_INCOMPLETE)
            confirms.append({
                "date": day,
                "session": session,
                "symbol": symbol,
                "t0": anc.t0,
                "t1": t1,
                "status": CAPTURE_BOUNDARY_INCOMPLETE,
                "reason": "LAST_CAPTURE_BEFORE_T1",
                "trend_slope": None,
                "endpoint_return": None,
                "p0": None,
                "p10": None,
                "fail_checkpoint": None,
                "decision_fire_time": None,
                "snapshot_cutoff": t1,
                "checkpoint_future_leak": 0,
            })
            return
        r = confirm_c1(symbol=symbol, t0=anc.t0, t1=t1, times=times, prices=prices)
        sm.close_active(r["status"])
        if r["status"] == CONFIRMED:
            fire = first_event_after_np(t1, global_times)
        else:
            fire = None
        confirms.append({
            "date": day,
            "session": session,
            "symbol": symbol,
            "t0": anc.t0,
            "t1": t1,
            "status": r["status"],
            "reason": r.get("reason"),
            "trend_slope": r.get("trend_slope"),
            "endpoint_return": r.get("endpoint_return"),
            "p0": r.get("p0"),
            "p10": r.get("p10"),
            "fail_checkpoint": r.get("fail_checkpoint"),
            "decision_fire_time": fire,
            "snapshot_cutoff": t1,
            "checkpoint_future_leak": r.get("checkpoint_future_leak") or 0,
        })

    for idx, r in enumerate(rows):
        grid_n += 1
        g = float(r["grid_epoch"])
        if sm.state == "ANCHOR_ACTIVE" and sm.active is not None and g + 1e-12 >= float(sm.active.t1):
            finish_active()
        prev_raw = sm.prev_raw
        raw = t1_raw(r)
        if _evaluable(r):
            eval_n += 1
        if raw:
            raw_true_n += 1
        fired = sm.on_grid(raw=raw, grid_epoch=g, day=day)
        if fired is not None and prev_raw is True and raw is True:
            persist_refire += 1
        if fired is None:
            continue
        trig = {
            "date": day,
            "session": session,
            "symbol": symbol,
            "t0": float(fired.t0),
            "grid_index": idx,
            "vol_percentile_60s": r.get("volume_percentile_60s"),
            "peer_n": r.get("rs_universe_n"),
            "previous_raw": False,
            "current_raw": True,
            "source_tick_t": r.get("source_tick_t"),
            "grid_time": r.get("grid_time"),
        }
        triggers.append(trig)
        if fired.status == SESSION_INCOMPLETE:
            confirms.append({
                "date": day,
                "session": session,
                "symbol": symbol,
                "t0": fired.t0,
                "t1": fired.t1,
                "status": SESSION_INCOMPLETE,
                "reason": "T1_AFTER_SESSION_END",
                "trend_slope": None,
                "endpoint_return": None,
                "p0": None,
                "p10": None,
                "fail_checkpoint": None,
                "decision_fire_time": None,
                "snapshot_cutoff": fired.t1,
                "checkpoint_future_leak": 0,
            })
    if sm.state == "ANCHOR_ACTIVE":
        finish_active()
    return {
        "triggers": triggers,
        "confirms": confirms,
        "persist_refire": persist_refire,
        "grid_n": grid_n,
        "eval_n": eval_n,
        "raw_true_n": raw_true_n,
        "unused_capture_class": capture_class,
    }


def run_prepared_day(
    *,
    day: str,
    capture_class: str,
    grids: dict[str, list[dict[str, Any]]],
    ticks_by: dict[str, list[dict[str, Any]]],
    global_times: np.ndarray,
    last_capture_t: Optional[float],
) -> dict[str, Any]:
    xs_samples, xs_leaks = xs_audit_rows(grids)
    triggers: list[dict[str, Any]] = []
    confirms: list[dict[str, Any]] = []
    persist = 0
    grid_n = eval_n = raw_true_n = 0
    for sess in ("AM", "PM"):
        for sym, rows in grids.items():
            sub = [r for r in rows if r.get("session") == sess]
            if not sub:
                continue
            tarr, parr = _price_arrays(ticks_by.get(sym) or [])
            out = walk_symbol_session(
                day=day,
                symbol=sym,
                session=sess,
                rows=sub,
                times=tarr,
                prices=parr,
                global_times=global_times,
                last_capture_t=last_capture_t,
                capture_class=capture_class,
            )
            triggers.extend(out["triggers"])
            confirms.extend(out["confirms"])
            persist += out["persist_refire"]
            grid_n += out["grid_n"]
            eval_n += out["eval_n"]
            raw_true_n += out["raw_true_n"]
    dup = 0
    seen = set()
    for t in triggers:
        key = (t["symbol"], round(float(t["t0"]), 6))
        if key in seen:
            dup += 1
        seen.add(key)
    ck_leak = sum(int(c.get("checkpoint_future_leak") or 0) for c in confirms)
    snap_leak = 0
    for c in confirms:
        fire = c.get("decision_fire_time")
        cut = c.get("snapshot_cutoff")
        if fire is not None and cut is not None and float(fire) <= float(cut) + 1e-12:
            snap_leak += 1
        if c.get("status") == CONFIRMED and c.get("max_checkpoint_source_time") is not None:
            pass
    return {
        "date": day,
        "capture_class": capture_class,
        "triggers": triggers,
        "confirms": confirms,
        "xs_samples": xs_samples,
        "cross_section_future_leak_count": xs_leaks,
        "checkpoint_future_leak_count": ck_leak,
        "decision_snapshot_future_leak_count": snap_leak,
        "TRUE_PERSISTENCE_REFIRE": persist,
        "duplicate_edge_fires": dup,
        "grid_evaluations": grid_n,
        "t1_evaluable_rows": eval_n,
        "raw_true_grid_rows": raw_true_n,
        "false_to_true_triggers": len(triggers),
        "unique_trigger_symbols": len({t["symbol"] for t in triggers}),
    }

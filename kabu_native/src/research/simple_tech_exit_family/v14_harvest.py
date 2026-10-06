"""V14 sealed Bid1 path + completed-bar D1/D2/D3 events. Clock = fill_time. No EXIT rule. No V13 writes."""
from __future__ import annotations

import gc
import os
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family import EMA_SLOPE_BARS, RCI_CROSS_LEVEL
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators, evaluable, execution_eligible, trend_up
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE
from research.simple_tech_exit_family.v14_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    EVENT_IDS,
    FILL_EVIDENCE,
    HORIZONS_SEC,
    PATH_WINDOW_SEC,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V14_CACHE = RESEARCH_CACHE / "v14_exit_state_path_rca"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _bps(bid: Any, fill: Any) -> Optional[float]:
    if not _finite(bid) or not _finite(fill) or float(fill) <= 0.0 or float(bid) <= 0.0:
        return None
    return (float(bid) / float(fill) - 1.0) * 10000.0


def _snap(board: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    return {
        "executable": bool(board["executable"][i]),
        "special": bool(board["special"][i]),
        "fresh_sec": float(board["fresh_sec"][i]),
        "bid": float(board["bid"][i]),
        "ask": float(board["ask"][i]),
        "bid_qty": float(board["bid_qty"][i]),
        "ask_qty": float(board["ask_qty"][i]),
    }


def _valid_exit_bid(board: dict[str, np.ndarray], i: int) -> bool:
    return execution_eligible(_snap(board, i)) and _finite(board["bid"][i]) and float(board["bid"][i]) > 0.0


def _e4(row: dict[str, Any]) -> dict[str, Any]:
    return dict((row.get("exec") or {}).get(DEVELOPMENT_CHALLENGER) or {})


def _d1_reason(ind: dict[str, np.ndarray], i: int) -> str:
    e9 = float(ind["ema9"][i]) if _finite(ind["ema9"][i]) else None
    e21 = float(ind["ema21"][i]) if _finite(ind["ema21"][i]) else None
    j = i - int(EMA_SLOPE_BARS)
    e21p = float(ind["ema21"][j]) if j >= 0 and _finite(ind["ema21"][j]) else None
    cross_ok = e9 is not None and e21 is not None and e9 > e21
    slope_ok = e21 is not None and e21p is not None and e21 > e21p
    if (not cross_ok) and (not slope_ok):
        return "BOTH_LOST"
    if not cross_ok:
        return "CROSS_LOST"
    return "SLOPE_LOST"


def walk_bid_path(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    window_end: float,
) -> dict[str, Any]:
    t = board.get("t")
    leak = {"PRE_FILL_QUOTE_N": 0, "INVALID_BID_SKIP_N": 0, "PATH_QUOTE_N": 0}
    empty = {
        "mfe_bps": None,
        "mae_bps": None,
        "time_to_mfe": None,
        "time_to_mae": None,
        "first_pos_t": None,
        "first_neg_t": None,
        "be_loss_t": None,
        "holds": {int(h): None for h in HORIZONS_SEC},
        "hold_t": {int(h): None for h in HORIZONS_SEC},
        "max_before": {180: None, 300: None},
        "min_before": {180: None, 300: None},
        "leak": leak,
    }
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return empty
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    mfe = mae = t_mfe = t_mae = first_pos = first_neg = be_loss = None
    saw_pos = False
    last_by_h: dict[int, tuple[float, float]] = {}
    max_h: dict[int, float] = {}
    min_h: dict[int, float] = {}
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            leak["PRE_FILL_QUOTE_N"] += 1
            continue
        if ti > float(window_end) + 1e-12:
            break
        if not _valid_exit_bid(board, i):
            leak["INVALID_BID_SKIP_N"] += 1
            continue
        mk = _bps(board["bid"][i], fill_px)
        if mk is None:
            leak["INVALID_BID_SKIP_N"] += 1
            continue
        leak["PATH_QUOTE_N"] += 1
        if mfe is None or float(mk) > float(mfe):
            mfe, t_mfe = float(mk), ti
        if mae is None or float(mk) < float(mae):
            mae, t_mae = float(mk), ti
        if first_pos is None and float(mk) > 0.0:
            first_pos = ti
        if first_neg is None and float(mk) < 0.0:
            first_neg = ti
        if float(mk) > 0.0:
            saw_pos = True
        elif saw_pos and be_loss is None and float(mk) <= 0.0:
            be_loss = ti
        dt = ti - float(fill_t)
        for h in HORIZONS_SEC:
            if dt <= float(h) + 1e-12:
                last_by_h[int(h)] = (ti, float(mk))
        for h in (180, 300):
            if dt <= float(h) + 1e-12:
                max_h[h] = float(mk) if h not in max_h else max(max_h[h], float(mk))
                min_h[h] = float(mk) if h not in min_h else min(min_h[h], float(mk))
    return {
        "mfe_bps": mfe,
        "mae_bps": mae,
        "time_to_mfe": (float(t_mfe) - float(fill_t)) if t_mfe is not None else None,
        "time_to_mae": (float(t_mae) - float(fill_t)) if t_mae is not None else None,
        "first_pos_t": first_pos,
        "first_neg_t": first_neg,
        "be_loss_t": be_loss,
        "holds": {int(h): (last_by_h[int(h)][1] if int(h) in last_by_h else None) for h in HORIZONS_SEC},
        "hold_t": {int(h): (last_by_h[int(h)][0] if int(h) in last_by_h else None) for h in HORIZONS_SEC},
        "max_before": {180: max_h.get(180), 300: max_h.get(300)},
        "min_before": {180: min_h.get(180), 300: min_h.get(300)},
        "leak": leak,
    }


def first_exit_bid(
    board: dict[str, np.ndarray],
    *,
    event_t: float,
    fill_px: float,
    sess_end: float,
) -> dict[str, Any]:
    t = board.get("t")
    out = {"exit_t": None, "exit_bid": None, "exit_pnl": None, "miss": True}
    if t is None or int(t.size) == 0:
        return out
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(event_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _valid_exit_bid(board, i):
            continue
        mk = _bps(board["bid"][i], fill_px)
        if mk is None:
            continue
        return {"exit_t": ti, "exit_bid": float(board["bid"][i]), "exit_pnl": float(mk), "miss": False}
    return out


def detect_events(
    ind: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
) -> dict[str, Any]:
    n = int(ind["close"].size)
    fin = ind.get("finalize_t")
    found: dict[str, Any] = {eid: None for eid in EVENT_IDS}
    if fin is None or n == 0:
        return found
    for i in range(n):
        et = float(fin[i]) if _finite(fin[i]) else None
        if et is None or et + 1e-12 < float(fill_t):
            continue
        if et > float(sess_end) + 1e-12:
            continue
        if found["D1_T3_CONTEXT_LOST"] is None and evaluable(i, n) and not trend_up(ind, i):
            found["D1_T3_CONTEXT_LOST"] = {
                "event_t": et,
                "bar_minute": float(ind["minute_epoch"][i]),
                "internal": _d1_reason(ind, i),
                **first_exit_bid(board, event_t=et, fill_px=fill_px, sess_end=sess_end),
            }
        if found["D2_RCI_RELAPSE_MINUS80"] is None and i >= 1:
            a = float(ind["rci9"][i - 1]) if _finite(ind["rci9"][i - 1]) else None
            b = float(ind["rci9"][i]) if _finite(ind["rci9"][i]) else None
            if a is not None and b is not None and a >= float(RCI_CROSS_LEVEL) and b < float(RCI_CROSS_LEVEL):
                found["D2_RCI_RELAPSE_MINUS80"] = {
                    "event_t": et,
                    "bar_minute": float(ind["minute_epoch"][i]),
                    "internal": None,
                    **first_exit_bid(board, event_t=et, fill_px=fill_px, sess_end=sess_end),
                }
        if found["D3_BB_LOWER_CLOSE_BREACH"] is None:
            cl = float(ind["close"][i]) if _finite(ind["close"][i]) else None
            lo = float(ind["bb_lower"][i]) if _finite(ind["bb_lower"][i]) else None
            if cl is not None and lo is not None and cl < lo:
                found["D3_BB_LOWER_CLOSE_BREACH"] = {
                    "event_t": et,
                    "bar_minute": float(ind["minute_epoch"][i]),
                    "internal": None,
                    **first_exit_bid(board, event_t=et, fill_px=fill_px, sess_end=sess_end),
                }
        if all(found[k] is not None for k in EVENT_IDS):
            break
    return found


def build_fill_record(sig: dict[str, Any], path: dict[str, Any], events: dict[str, Any], *, window_end: float) -> dict[str, Any]:
    ex = _e4(sig)
    fill_t = float(ex["fill_t"])
    rec = {
        "date": sig.get("date"),
        "symbol": str(sig.get("symbol") or "").replace(".T", ""),
        "t0": float(sig.get("t0") or 0.0),
        "fill_t": fill_t,
        "fill_price": ex.get("fill_price"),
        "limit_price": ex.get("limit_price"),
        "inside_collapsed": bool(ex.get("collapsed_to_bid") or sig.get("inside_collapsed_to_bid")),
        "wait_sec": (fill_t - float(sig["t0"])) if _finite(sig.get("t0")) else None,
        "evidence": ex.get("evidence") or FILL_EVIDENCE,
        "window_end": float(window_end),
        "MFE_BID_BPS": path.get("mfe_bps"),
        "MAE_BID_BPS": path.get("mae_bps"),
        "TIME_TO_MFE": path.get("time_to_mfe"),
        "TIME_TO_MAE": path.get("time_to_mae"),
        "FIRST_POSITIVE_TIME": path.get("first_pos_t"),
        "FIRST_NEGATIVE_TIME": path.get("first_neg_t"),
        "BE_LOSS_TIME": path.get("be_loss_t"),
        "MAX_BEFORE_180": (path.get("max_before") or {}).get(180),
        "MIN_BEFORE_180": (path.get("min_before") or {}).get(180),
        "MAX_BEFORE_300": (path.get("max_before") or {}).get(300),
        "MIN_BEFORE_300": (path.get("min_before") or {}).get(300),
        "PATH_QUOTE_N": int((path.get("leak") or {}).get("PATH_QUOTE_N") or 0),
        "events": events,
        "path_leak": path.get("leak") or {},
        "passive_filled": True,
        "executable_signal": True,
    }
    holds = path.get("holds") or {}
    hold_t = path.get("hold_t") or {}
    for h in (60, 180, 300):
        rec[f"hold_{h}"] = holds.get(h)
        rec[f"hold_t_{h}"] = hold_t.get(h)
        rec[f"markout_{h}"] = holds.get(h)
    return rec


def replay_v14_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    fills = list(payload.get("fills") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FUTURE_QUOTE_N": 0,
        "PRE_FILL_QUOTE_N": 0,
        "PRE_FILL_EVENT_N": 0,
        "UNFILLED_VIRTUAL_N": 0,
        "C14_REPLAY_N": 0,
        "EXIT_POLICY_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "BAR_INTEG_FAIL_N": 0,
        "EXIT_BID_MISS_N": 0,
        "FILL_N": 0,
    }
    if not fills:
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "v12_spec_sha": V12_SPEC_SHA256_EXPECTED,
            "events_n": 0,
            "rows": [],
            "leak": leak,
            "elapsed_sec": 0.0,
            "blocker": None,
        }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in fills if _bare(r.get("symbol"))}
        bufs: dict[str, _Buf] = {s: _Buf() for s in needed}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in needed}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in needed:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
            row = _board_row(pay, float(et))
            bufs[sym].append(row)
            builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 200000 == 0:
                print(f"{day} v14 stream kept={events_n} last_et={last_et}", flush=True)
        inds: dict[str, dict[str, np.ndarray]] = {}
        views: dict[str, dict[str, np.ndarray]] = {}
        for s in needed:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            if not integ.get("ok"):
                leak["BAR_INTEG_FAIL_N"] += 1
            views[s] = bufs[s].view()
            inds[s] = attach_indicators(raw)
        rows = []
        for sig in fills:
            ex = _e4(sig)
            if not ex.get("filled") or not _finite(ex.get("fill_t")) or not _finite(ex.get("fill_price")):
                leak["UNFILLED_VIRTUAL_N"] += 1
                continue
            fill_t = float(ex["fill_t"])
            t0 = float(sig.get("t0") or 0.0)
            if fill_t + 1e-12 < t0 or fill_t > t0 + float(E4_WAIT_BUDGET_SEC) + 1e-12:
                leak["FUTURE_QUOTE_N"] += 1
            s = _bare(sig.get("symbol"))
            window_end = min(fill_t + float(PATH_WINDOW_SEC), float(am_end))
            path = walk_bid_path(views[s], fill_t=fill_t, fill_px=float(ex["fill_price"]), window_end=window_end)
            leak["PRE_FILL_QUOTE_N"] += int((path.get("leak") or {}).get("PRE_FILL_QUOTE_N") or 0)
            events = detect_events(
                inds[s], views[s], fill_t=fill_t, fill_px=float(ex["fill_price"]), sess_end=float(am_end)
            )
            for ev in events.values():
                if not ev:
                    continue
                if float(ev["event_t"]) + 1e-12 < fill_t:
                    leak["PRE_FILL_EVENT_N"] += 1
                if ev.get("miss"):
                    leak["EXIT_BID_MISS_N"] += 1
            rows.append(build_fill_record(sig, path, events, window_end=window_end))
            leak["FILL_N"] += 1
        print(f"{day} v14 kept_events={events_n} fills={len(rows)} last_et={last_et}", flush=True)
        del bufs, builders, views, inds
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "v12_spec_sha": V12_SPEC_SHA256_EXPECTED,
            "events_n": events_n,
            "last_et": last_et,
            "rows": rows,
            "leak": leak,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_v14_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "v12_spec_sha": body.get("v12_spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v14_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

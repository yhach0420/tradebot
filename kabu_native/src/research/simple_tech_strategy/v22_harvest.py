"""V22 quantity-aware E4/FIXED180 capacity replay. No sizing policy. No V18 cache copy."""
from __future__ import annotations

import gc
import math
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
from research.e1_x34a_execution_policy.arms import _row_ask_ok, find_ask_cross_fill
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.v11_harvest import quote_row
from research.simple_tech_entry_family.v12_harvest import _empty_arm, _finite, improve_1tick_limit
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE as EXIT_CACHE
from research.simple_tech_exit_family.v14_harvest import first_exit_bid
from research.simple_tech_strategy.isolation import RESEARCH_CACHE
from research.simple_tech_strategy.v22_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    HOLD_SEC,
    HYPOTHETICAL_NOTIONAL_YEN,
    LOT_SHARES,
    SHARES_100,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V18_CACHE = EXIT_CACHE / "v18_fixed180_exit"
V22_CACHE = RESEARCH_CACHE / "v22_sizing_execution_capacity_rca"


def lot_floor(qty: Any, lot: int = LOT_SHARES) -> int:
    if not _finite(qty) or float(qty) < float(lot) - 1e-12:
        return 0
    return int(math.floor((float(qty) + 1e-12) / float(lot))) * int(lot)


def shares_for_notional(price: Any, notional: float = HYPOTHETICAL_NOTIONAL_YEN, lot: int = LOT_SHARES) -> Optional[int]:
    if not _finite(price) or float(price) <= 0.0:
        return None
    raw = float(notional) / float(price)
    n = int(math.ceil(raw / float(lot) - 1e-12)) * int(lot)
    return max(n, int(lot))


def scan_ask_cross_capacity(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit_price: float,
    sess_end: float,
) -> dict[str, Any]:
    """Max visible Ask1 qty on ASK_CROSS_CONSERVATIVE quotes in the frozen 5s wait. No summing."""
    t = board.get("t")
    out = {"max_ask_cross_qty": None, "ask_cross_n": 0, "qty_missing_n": 0}
    if t is None or int(t.size) == 0:
        return out
    lim_t = min(float(t0) + float(wait_sec), float(sess_end))
    i0 = int(np.searchsorted(t, float(t0), side="left"))
    mx = None
    n = 0
    miss = 0
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(t0):
            continue
        if ti > lim_t + 1e-12:
            break
        if not _row_ask_ok(board, i):
            continue
        ask = float(board["ask"][i])
        if not np.isfinite(ask) or ask <= 0.0 or ask > float(limit_price) + 1e-12:
            continue
        qty = board["ask_qty"][i]
        if not np.isfinite(qty):
            miss += 1
            continue
        n += 1
        mx = float(qty) if mx is None else max(float(mx), float(qty))
    out["max_ask_cross_qty"] = mx
    out["ask_cross_n"] = int(n)
    out["qty_missing_n"] = int(miss)
    return out


def _qty_at_time(board: dict[str, np.ndarray], ts: Any, *, side: str) -> Optional[float]:
    t = board.get("t")
    if t is None or int(t.size) == 0 or not _finite(ts):
        return None
    i = int(np.searchsorted(t, float(ts), side="left"))
    best = None
    best_dt = 1e-9
    n = int(t.size)
    for j in (i - 1, i, i + 1):
        if 0 <= j < n:
            dt = abs(float(t[j]) - float(ts))
            if dt <= best_dt:
                best_dt = dt
                best = j
    if best is None:
        return None
    key = "ask_qty" if side == "ask" else "bid_qty"
    q = board[key][int(best)]
    return float(q) if np.isfinite(q) else None


def simulate_e4_capacity(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec = dict(rec)
    rec["ENTRY_ORDER_PRICE_CAUSAL"] = None
    rec["ENTRY_MAX_FULL_FILL_SHARES"] = 0
    rec["FILL_SNAPSHOT_SHARES"] = 0
    rec["filled_100"] = False
    rec["fill_t_100"] = None
    rec["fill_price_100"] = None
    rec["fill_ask_qty_100"] = None
    rec["max_ask_cross_qty"] = None
    rec["ask_cross_n"] = 0
    if not rec.get("executable_signal") or not _finite(rec.get("bid0")) or not _finite(rec.get("ask0")):
        leak["NOT_ELIGIBLE_N"] = int(leak.get("NOT_ELIGIBLE_N") or 0) + 1
        rec["inside_collapsed_to_bid"] = None
        rec["inside_limit"] = None
        rec["bid_limit"] = None
        rec["tick"] = None
        rec["exec"] = {DEVELOPMENT_CHALLENGER: _empty_arm("IMPROVE_1TICK", float(E4_WAIT_BUDGET_SEC), None, collapsed=False)}
        return rec
    t0 = float(rec["t0"])
    bid0 = float(rec["bid0"])
    ask0 = float(rec["ask0"])
    inside_lim, collapsed, tick = improve_1tick_limit(bid0, ask0)
    rec["inside_collapsed_to_bid"] = bool(collapsed)
    rec["inside_limit"] = float(inside_lim)
    rec["bid_limit"] = float(bid0)
    rec["tick"] = float(tick)
    rec["ENTRY_ORDER_PRICE_CAUSAL"] = float(inside_lim)
    if collapsed:
        leak["INSIDE_COLLAPSE_N"] = int(leak.get("INSIDE_COLLAPSE_N") or 0) + 1
    fill = find_ask_cross_fill(
        board,
        t0=float(t0),
        wait_sec=float(E4_WAIT_BUDGET_SEC),
        limit_price=float(inside_lim),
        sess_end=float(am_end),
        require_executable_continuous=True,
    )
    filled = bool(fill.get("filled"))
    arm = {
        "filled": filled,
        "fill_price": fill.get("fill_price") if filled else None,
        "fill_t": fill.get("fill_t") if filled else None,
        "limit_price": float(inside_lim),
        "wait_budget_sec": float(E4_WAIT_BUDGET_SEC),
        "waited_sec": (float(fill["fill_t"]) - float(t0)) if filled and _finite(fill.get("fill_t")) else None,
        "family": "IMPROVE_1TICK",
        "collapsed_to_bid": bool(collapsed),
        "evidence": FILL_EVIDENCE if filled else None,
        "fill_reason": None if filled else fill.get("reason"),
        "nonfill_class": None,
        "cross_ask_qty": fill.get("cross_ask_qty") if filled else None,
    }
    rec["exec"] = {DEVELOPMENT_CHALLENGER: arm}
    cap = scan_ask_cross_capacity(
        board, t0=float(t0), wait_sec=float(E4_WAIT_BUDGET_SEC), limit_price=float(inside_lim), sess_end=float(am_end)
    )
    rec["max_ask_cross_qty"] = cap.get("max_ask_cross_qty")
    rec["ask_cross_n"] = cap.get("ask_cross_n")
    leak["ASK_CROSS_QTY_MISS_N"] = int(leak.get("ASK_CROSS_QTY_MISS_N") or 0) + int(cap.get("qty_missing_n") or 0)
    if filled:
        leak["ASK_CROSS_FILL_N"] = int(leak.get("ASK_CROSS_FILL_N") or 0) + 1
        rec["filled_100"] = True
        rec["fill_t_100"] = fill.get("fill_t")
        rec["fill_price_100"] = fill.get("fill_price")
        rec["fill_ask_qty_100"] = fill.get("cross_ask_qty")
        rec["FILL_SNAPSHOT_SHARES"] = lot_floor(fill.get("cross_ask_qty"))
        rec["ENTRY_MAX_FULL_FILL_SHARES"] = lot_floor(cap.get("max_ask_cross_qty"))
        if rec["ENTRY_MAX_FULL_FILL_SHARES"] < int(SHARES_100):
            leak["ENTRY_CAPACITY_LT_100_N"] = int(leak.get("ENTRY_CAPACITY_LT_100_N") or 0) + 1
        if not _finite(fill.get("cross_ask_qty")):
            leak["MISSING_ENTRY_QTY_N"] = int(leak.get("MISSING_ENTRY_QTY_N") or 0) + 1
    else:
        rec["ENTRY_MAX_FULL_FILL_SHARES"] = 0
        leak["E4_UNFILLED_N"] = int(leak.get("E4_UNFILLED_N") or 0) + 1
    return rec


def attach_exit_capacity(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec = dict(rec)
    rec["EXIT_MAX_FULL_SHARES"] = None
    rec["exit_bid_qty"] = None
    rec["actual_exit_quote_time"] = None
    rec["exit_bid"] = None
    rec["scheduled_exit_time"] = None
    rec["session_clamped"] = None
    rec["ROUNDTRIP_MAX_FULL_SHARES"] = None
    rec["ROUNDTRIP_CAPACITY_NOTIONAL_YEN"] = None
    rec["THEORETICAL_1M_SHARES"] = None
    rec["NORMALIZED_1M_EXECUTION_FEASIBLE"] = None
    if not rec.get("filled_100") or not _finite(rec.get("fill_t_100")):
        return rec
    fill_t = float(rec["fill_t_100"])
    fill_px = float(rec["fill_price_100"])
    raw_sched = float(fill_t) + float(HOLD_SEC)
    session_clamped = raw_sched > float(am_end) + 1e-12
    scheduled_t = min(raw_sched, float(am_end))
    rec["scheduled_exit_time"] = float(scheduled_t)
    rec["session_clamped"] = bool(session_clamped)
    if session_clamped:
        leak["SESSION_CLAMP_N"] = int(leak.get("SESSION_CLAMP_N") or 0) + 1
    pack = first_exit_bid(board, event_t=float(scheduled_t), fill_px=float(fill_px), sess_end=float(am_end))
    if pack.get("miss"):
        leak["EXIT_BID_MISS_N"] = int(leak.get("EXIT_BID_MISS_N") or 0) + 1
        rec["EXIT_MISS"] = True
        return rec
    rec["EXIT_MISS"] = False
    rec["actual_exit_quote_time"] = pack.get("exit_t")
    rec["exit_bid"] = pack.get("exit_bid")
    qty = _qty_at_time(board, pack.get("exit_t"), side="bid")
    rec["exit_bid_qty"] = qty
    if not _finite(qty):
        leak["MISSING_EXIT_QTY_N"] = int(leak.get("MISSING_EXIT_QTY_N") or 0) + 1
        rec["EXIT_MAX_FULL_SHARES"] = 0
    else:
        rec["EXIT_MAX_FULL_SHARES"] = lot_floor(qty)
        if int(rec["EXIT_MAX_FULL_SHARES"]) < int(SHARES_100):
            leak["EXIT_CAPACITY_LT_100_N"] = int(leak.get("EXIT_CAPACITY_LT_100_N") or 0) + 1
    entry_max = int(rec.get("ENTRY_MAX_FULL_FILL_SHARES") or 0)
    exit_max = int(rec.get("EXIT_MAX_FULL_SHARES") or 0)
    rec["ROUNDTRIP_MAX_FULL_SHARES"] = int(min(entry_max, exit_max))
    px = rec.get("ENTRY_ORDER_PRICE_CAUSAL")
    if _finite(px) and rec["ROUNDTRIP_MAX_FULL_SHARES"] > 0:
        rec["ROUNDTRIP_CAPACITY_NOTIONAL_YEN"] = float(rec["ROUNDTRIP_MAX_FULL_SHARES"]) * float(px)
    rec["THEORETICAL_1M_SHARES"] = shares_for_notional(px)
    if rec["THEORETICAL_1M_SHARES"] is not None:
        rec["NORMALIZED_1M_EXECUTION_FEASIBLE"] = bool(
            int(rec["ROUNDTRIP_MAX_FULL_SHARES"]) >= int(rec["THEORETICAL_1M_SHARES"])
        )
    leak["FILL_N"] = int(leak.get("FILL_N") or 0) + 1
    return rec


def replay_v22_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "C14_REPLAY_N": 0,
        "QUEUE_FILL_N": 0,
        "PARTIAL_FILL_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "VWAP_N": 0,
        "DEEPER_BOOK_N": 0,
        "SLIPPAGE_N": 0,
        "EXIT_WAIT_EXTRA_N": 0,
        "NOT_ELIGIBLE_N": 0,
        "INSIDE_COLLAPSE_N": 0,
        "ASK_CROSS_FILL_N": 0,
        "E4_UNFILLED_N": 0,
        "MISSING_ENTRY_QTY_N": 0,
        "MISSING_EXIT_QTY_N": 0,
        "ASK_CROSS_QTY_MISS_N": 0,
        "ENTRY_CAPACITY_LT_100_N": 0,
        "EXIT_CAPACITY_LT_100_N": 0,
        "EXIT_BID_MISS_N": 0,
        "SESSION_CLAMP_N": 0,
        "FILL_N": 0,
        "V18_CACHE_COPY_N": 0,
        "ENTRY_CHANGE_N": 0,
        "EXIT_CHANGE_N": 0,
        "SIZING_SEARCH_N": 0,
    }
    empty = {
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
    if not signals:
        return empty
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in signals if _bare(r.get("symbol"))}
        bufs: dict[str, _Buf] = {s: _Buf() for s in needed}
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
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 200000 == 0:
                print(f"{day} v22 stream kept={events_n} last_et={last_et}", flush=True)
        views = {s: bufs[s].view() for s in needed}
        rows = []
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rec = quote_row(sig, views[s], am_end=am_end, leak=leak)
            rec = simulate_e4_capacity(rec, views[s], am_end=am_end, leak=leak)
            rec = attach_exit_capacity(rec, views[s], am_end=am_end, leak=leak)
            rows.append(rec)
        print(f"{day} v22 kept_events={events_n} signals={len(rows)} fills={leak.get('FILL_N')} last_et={last_et}", flush=True)
        del bufs, views
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


def _slim_row(r: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "date",
        "symbol",
        "t0",
        "executable_signal",
        "bid0",
        "ask0",
        "inside_collapsed_to_bid",
        "inside_limit",
        "bid_limit",
        "tick",
        "exec",
        "ENTRY_ORDER_PRICE_CAUSAL",
        "ENTRY_MAX_FULL_FILL_SHARES",
        "FILL_SNAPSHOT_SHARES",
        "filled_100",
        "fill_t_100",
        "fill_price_100",
        "fill_ask_qty_100",
        "max_ask_cross_qty",
        "ask_cross_n",
        "EXIT_MAX_FULL_SHARES",
        "exit_bid_qty",
        "actual_exit_quote_time",
        "exit_bid",
        "scheduled_exit_time",
        "session_clamped",
        "EXIT_MISS",
        "ROUNDTRIP_MAX_FULL_SHARES",
        "ROUNDTRIP_CAPACITY_NOTIONAL_YEN",
        "THEORETICAL_1M_SHARES",
        "NORMALIZED_1M_EXECUTION_FEASIBLE",
    )
    return {k: r.get(k) for k in keys}


def save_v22_day_cache(path: Path, body: dict[str, Any]) -> None:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        raise RuntimeError("V22 must not write V18 cache.")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_slim_row(r) for r in list(body.get("rows") or [])]
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
            "rows": rows,
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v22_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        return {}
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

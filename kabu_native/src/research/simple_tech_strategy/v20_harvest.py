"""V20 sealed Bid-path occupancy/MTM harvest. Frozen V19 trades. No EXIT re-search. No V18 cache copy."""
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
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE as EXIT_CACHE
from research.simple_tech_exit_family.v14_harvest import _snap, _valid_exit_bid
from research.simple_tech_exit_family.v18_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_strategy.isolation import RESEARCH_CACHE
from research.simple_tech_strategy.v20_spec import (
    E4_WAIT_BUDGET_SEC,
    MIN_QTY,
    SHARES,
    V19_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V18_CACHE = EXIT_CACHE / "v18_fixed180_exit"
V19_CACHE = EXIT_CACHE / "v19_exit_structure_verification"
V20_CACHE = RESEARCH_CACHE / "v20_frozen_portfolio_economics"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
        return x == x
    except (TypeError, ValueError):
        return False


def _qty_ok(v: Any) -> bool:
    return _finite(v) and float(v) >= float(MIN_QTY) - 1e-12


def load_v19_day_trades(day: str) -> dict[str, Any]:
    path = V19_CACHE / f"day_{day}.json"
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        return {}
    return load_day_cache(path, V19_SPEC_SHA256_EXPECTED)


def _row_at(board: dict[str, np.ndarray], ts: float) -> dict[str, Any]:
    t = board.get("t")
    out = {"i": None, "t": None, "bid": None, "ask": None, "bid_qty": None, "ask_qty": None, "valid_exit": False}
    if t is None or int(t.size) == 0 or not _finite(ts):
        return out
    i = int(np.searchsorted(t, float(ts), side="left"))
    best = None
    best_dt = 1e-6
    n = int(t.size)
    for j in (i - 1, i, i + 1):
        if 0 <= j < n:
            dt = abs(float(t[j]) - float(ts))
            if dt <= best_dt:
                best_dt = dt
                best = j
    if best is None:
        return out
    i = int(best)
    snap = _snap(board, i)
    out.update(
        {
            "i": i,
            "t": float(t[i]),
            "bid": snap.get("bid"),
            "ask": snap.get("ask"),
            "bid_qty": snap.get("bid_qty"),
            "ask_qty": snap.get("ask_qty"),
            "valid_exit": bool(_valid_exit_bid(board, i)),
        }
    )
    return out


def _mark_yen(bid: Any, fill_px: float) -> Optional[float]:
    if not _finite(bid) or not _finite(fill_px):
        return None
    return (float(bid) - float(fill_px)) * float(SHARES)


def replay_v20_day(payload: dict[str, Any]) -> dict[str, Any]:
    """Stream sealed Capture for fill/exit qty evidence and Bid MTM. Does not recompute EXIT."""
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    trades = [dict(r) for r in list(payload.get("trades") or [])]
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FUTURE_QUOTE_N": 0,
        "PRE_FILL_EVENT_N": 0,
        "MISSING_ENTRY_QUOTE_N": 0,
        "MISSING_EXIT_QUOTE_N": 0,
        "MISSING_ENTRY_QTY_N": 0,
        "MISSING_EXIT_QTY_N": 0,
        "TIMESTAMP_INVERSION_N": 0,
        "FUTURE_USE_N": 0,
        "UNFILLED_VIRTUAL_N": 0,
        "C14_REPLAY_N": 0,
        "ENTRY_CHANGE_N": 0,
        "EXIT_CHANGE_N": 0,
        "HOLD_SEARCH_N": 0,
        "POSITION_CAP_N": 0,
        "SAME_SYMBOL_MERGE_N": 0,
        "V18_CACHE_COPY_N": 0,
        "FEE_ASSUMPTION_N": 0,
        "ANNUALIZE_N": 0,
        "FILL_N": 0,
    }
    empty = {
        "ok": True,
        "date": day,
        "spec_sha": spec_sha,
        "v12_spec_sha": V12_SPEC_SHA256_EXPECTED,
        "v19_spec_sha": V19_SPEC_SHA256_EXPECTED,
        "events_n": 0,
        "rows": trades,
        "mtm_points": [],
        "leak": leak,
        "elapsed_sec": 0.0,
        "blocker": None,
    }
    if not trades:
        return empty
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in trades if _bare(r.get("symbol"))}
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
                print(f"{day} v20 stream kept={events_n} last_et={last_et}", flush=True)
        views: dict[str, dict[str, np.ndarray]] = {s: bufs[s].view() for s in needed}

        indexed: list[dict[str, Any]] = []
        for rec in trades:
            s = _bare(rec.get("symbol"))
            board = views.get(s) or {}
            fill_t = rec.get("fill_t")
            exit_t = rec.get("actual_exit_quote_time")
            t0 = rec.get("t0")
            sched = rec.get("scheduled_exit_time")
            fill_snap = _row_at(board, float(fill_t)) if _finite(fill_t) else {}
            exit_snap = _row_at(board, float(exit_t)) if _finite(exit_t) else {}
            rec = dict(rec)
            rec["fill_ask_qty"] = fill_snap.get("ask_qty")
            rec["fill_bid_qty"] = fill_snap.get("bid_qty")
            rec["exit_bid_qty"] = exit_snap.get("bid_qty")
            rec["entry_quote_missing"] = fill_snap.get("i") is None
            rec["exit_quote_missing"] = exit_snap.get("i") is None
            rec["entry_qty_missing"] = not _qty_ok(fill_snap.get("ask_qty"))
            rec["exit_qty_missing"] = not _qty_ok(exit_snap.get("bid_qty"))
            rec["mtm_mae_yen"] = None
            rec["mtm_mfe_yen"] = None
            if rec["entry_quote_missing"]:
                leak["MISSING_ENTRY_QUOTE_N"] += 1
            if rec["exit_quote_missing"]:
                leak["MISSING_EXIT_QUOTE_N"] += 1
            if rec["entry_qty_missing"]:
                leak["MISSING_ENTRY_QTY_N"] += 1
            if rec["exit_qty_missing"]:
                leak["MISSING_EXIT_QTY_N"] += 1
            if _finite(t0) and _finite(fill_t) and float(fill_t) + 1e-12 < float(t0):
                leak["TIMESTAMP_INVERSION_N"] += 1
                rec["timestamp_inversion"] = True
            elif _finite(fill_t) and _finite(exit_t) and float(exit_t) + 1e-12 < float(fill_t):
                leak["TIMESTAMP_INVERSION_N"] += 1
                rec["timestamp_inversion"] = True
            elif _finite(sched) and _finite(exit_t) and float(exit_t) + 1e-12 < float(sched):
                leak["TIMESTAMP_INVERSION_N"] += 1
                rec["timestamp_inversion"] = True
            else:
                rec["timestamp_inversion"] = False
            if _finite(fill_t) and _finite(t0) and float(fill_t) > float(t0) + float(E4_WAIT_BUDGET_SEC) + 1e-12:
                leak["FUTURE_QUOTE_N"] += 1
            if _finite(exit_t) and float(exit_t) > float(am_end) + 1e-12:
                leak["FUTURE_USE_N"] += 1
                rec["future_use"] = True
            else:
                rec["future_use"] = False
            if _finite(exit_t) and _finite(fill_t) and float(exit_t) + 1e-12 < float(fill_t):
                leak["PRE_FILL_EVENT_N"] += 1
            indexed.append(rec)
            leak["FILL_N"] += 1

        open_pos: dict[int, dict[str, Any]] = {}
        by_sym: dict[str, list[int]] = {s: [] for s in needed}
        for i, rec in enumerate(indexed):
            by_sym[_bare(rec.get("symbol"))].append(i)
        events: list[tuple[float, int, int]] = []
        for i, rec in enumerate(indexed):
            if _finite(rec.get("fill_t")):
                events.append((float(rec["fill_t"]), 1, i))
            if _finite(rec.get("actual_exit_quote_time")):
                events.append((float(rec["actual_exit_quote_time"]), 0, i))
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        cursor = {s: 0 for s in needed}
        realized_today = 0.0
        last_bucket = None
        last_unreal = 0.0
        mtm_points: list[dict[str, Any]] = []
        mae: dict[int, float] = {}
        mfe: dict[int, float] = {}

        def _unreal() -> float:
            tot = 0.0
            for rec in open_pos.values():
                mk = rec.get("mark_yen")
                if _finite(mk):
                    tot += float(mk)
            return tot

        def _emit(ts: float) -> None:
            nonlocal last_bucket, last_unreal
            bucket = int(float(ts))
            u = _unreal()
            if last_bucket == bucket and abs(u - last_unreal) < 1e-9:
                if mtm_points:
                    mtm_points[-1] = {"t": float(ts), "realized_today": float(realized_today), "unrealized": float(u)}
                return
            mtm_points.append({"t": float(ts), "realized_today": float(realized_today), "unrealized": float(u)})
            last_bucket = bucket
            last_unreal = u

        def _apply_quote(sym: str, board: dict[str, np.ndarray], i: int) -> None:
            if not _valid_exit_bid(board, i):
                return
            bid = float(board["bid"][i])
            ti = float(board["t"][i])
            changed = False
            for idx, rec in list(open_pos.items()):
                if _bare(rec.get("symbol")) != sym:
                    continue
                mk = _mark_yen(bid, float(rec["fill_price"]))
                rec["mark_yen"] = mk
                if _finite(mk):
                    mae[idx] = float(mk) if idx not in mae else min(mae[idx], float(mk))
                    mfe[idx] = float(mk) if idx not in mfe else max(mfe[idx], float(mk))
                    changed = True
            if changed:
                _emit(ti)

        ei = 0
        while ei < len(events):
            et, kind, idx = events[ei]
            for s, board in views.items():
                t = board.get("t")
                if t is None:
                    continue
                n = int(t.size)
                c = cursor[s]
                while c < n and float(t[c]) + 1e-12 < float(et):
                    _apply_quote(s, board, c)
                    c += 1
                cursor[s] = c
            same: list[tuple[float, int, int]] = []
            while ei < len(events) and abs(events[ei][0] - et) <= 1e-12:
                same.append(events[ei])
                ei += 1
            for s, board in views.items():
                t = board.get("t")
                if t is None:
                    continue
                n = int(t.size)
                c = cursor[s]
                while c < n and abs(float(t[c]) - float(et)) <= 1e-12:
                    _apply_quote(s, board, c)
                    c += 1
                cursor[s] = c
            for _ts, kind_i, idx_i in same:
                rec = indexed[idx_i]
                if kind_i == 0:
                    if idx_i in open_pos:
                        if _finite(rec.get("pnl_yen_100")):
                            realized_today += float(rec["pnl_yen_100"])
                        open_pos.pop(idx_i, None)
                        _emit(_ts)
                else:
                    rec["mark_yen"] = 0.0
                    open_pos[idx_i] = rec
                    _emit(_ts)

        if open_pos:
            for s, board in views.items():
                t = board.get("t")
                if t is None:
                    continue
                n = int(t.size)
                c = cursor[s]
                while c < n:
                    _apply_quote(s, board, c)
                    c += 1
                cursor[s] = c

        for i, rec in enumerate(indexed):
            rec["mtm_mae_yen"] = mae.get(i)
            rec["mtm_mfe_yen"] = mfe.get(i)
            rec.pop("mark_yen", None)

        print(f"{day} v20 kept_events={events_n} fills={len(indexed)} mtm_pts={len(mtm_points)} last_et={last_et}", flush=True)
        del bufs, views
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "v12_spec_sha": V12_SPEC_SHA256_EXPECTED,
            "v19_spec_sha": V19_SPEC_SHA256_EXPECTED,
            "events_n": events_n,
            "last_et": last_et,
            "rows": indexed,
            "mtm_points": mtm_points,
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


def save_v20_day_cache(path: Path, body: dict[str, Any]) -> None:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        raise RuntimeError("V20 must not write V18 cache.")
    if V19_CACHE in path.parents or path.parent == V19_CACHE:
        raise RuntimeError("V20 must not write V19 cache.")
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "v12_spec_sha": body.get("v12_spec_sha"),
            "v19_spec_sha": body.get("v19_spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "mtm_points": body.get("mtm_points"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v20_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    if V18_CACHE in path.parents or path.parent == V18_CACHE:
        return {}
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v19_spec_sha") or "") != V19_SPEC_SHA256_EXPECTED:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

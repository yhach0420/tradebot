"""V15 sealed Bid1 G1 walk. Clock = fill_time. No bars. No new indicators. No EXIT rule."""
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
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE
from research.simple_tech_exit_family.v14_harvest import _bps, _e4, _valid_exit_bid
from research.simple_tech_exit_family.v15_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    FILL_EVIDENCE,
    HORIZONS_SEC,
    PATH_WINDOW_SEC,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V15_CACHE = RESEARCH_CACHE / "v15_be_reloss_mechanism"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def walk_g1_path(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    window_end: float,
) -> dict[str, Any]:
    t = board.get("t")
    leak = {"PRE_FILL_QUOTE_N": 0, "INVALID_BID_SKIP_N": 0, "PATH_QUOTE_N": 0, "PRE_FILL_EVENT_N": 0}
    empty = {
        "mfe_bps": None,
        "mae_bps": None,
        "time_to_mfe": None,
        "time_to_mae": None,
        "first_pos_t": None,
        "first_neg_t": None,
        "arm_t": None,
        "event_t": None,
        "event_bid": None,
        "event_pnl": None,
        "event_miss": True,
        "holds": {int(h): None for h in HORIZONS_SEC},
        "hold_t": {int(h): None for h in HORIZONS_SEC},
        "max_before": {180: None, 300: None},
        "min_before": {180: None, 300: None},
        "max_after": {180: None, 300: None},
        "leak": leak,
    }
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return empty
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    mfe = mae = t_mfe = t_mae = first_pos = first_neg = None
    arm_t = event_t = event_bid = event_pnl = None
    last_by_h: dict[int, tuple[float, float]] = {}
    max_h: dict[int, float] = {}
    min_h: dict[int, float] = {}
    max_after: dict[int, float] = {}
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
        bid = float(board["bid"][i])
        mk = _bps(bid, fill_px)
        if mk is None:
            leak["INVALID_BID_SKIP_N"] += 1
            continue
        leak["PATH_QUOTE_N"] += 1
        if mfe is None or float(mk) > float(mfe):
            mfe, t_mfe = float(mk), ti
        if mae is None or float(mk) < float(mae):
            mae, t_mae = float(mk), ti
        if first_pos is None and float(bid) > float(fill_px):
            first_pos = ti
        if first_neg is None and float(mk) < 0.0:
            first_neg = ti
        if arm_t is None and float(bid) > float(fill_px):
            arm_t = ti
        elif arm_t is not None and event_t is None and float(bid) <= float(fill_px):
            if ti + 1e-12 < float(fill_t):
                leak["PRE_FILL_EVENT_N"] += 1
            event_t = ti
            event_bid = bid
            event_pnl = float(mk)
        dt = ti - float(fill_t)
        for h in HORIZONS_SEC:
            if dt <= float(h) + 1e-12:
                last_by_h[int(h)] = (ti, float(mk))
        for h in (180, 300):
            if dt <= float(h) + 1e-12:
                max_h[h] = float(mk) if h not in max_h else max(max_h[h], float(mk))
                min_h[h] = float(mk) if h not in min_h else min(min_h[h], float(mk))
            if event_t is not None and ti > float(event_t) + 1e-12 and dt <= float(h) + 1e-12:
                max_after[h] = float(mk) if h not in max_after else max(max_after[h], float(mk))
    return {
        "mfe_bps": mfe,
        "mae_bps": mae,
        "time_to_mfe": (float(t_mfe) - float(fill_t)) if t_mfe is not None else None,
        "time_to_mae": (float(t_mae) - float(fill_t)) if t_mae is not None else None,
        "first_pos_t": first_pos,
        "first_neg_t": first_neg,
        "arm_t": arm_t,
        "event_t": event_t,
        "event_bid": event_bid,
        "event_pnl": event_pnl,
        "event_miss": event_t is None,
        "holds": {int(h): (last_by_h[int(h)][1] if int(h) in last_by_h else None) for h in HORIZONS_SEC},
        "hold_t": {int(h): (last_by_h[int(h)][0] if int(h) in last_by_h else None) for h in HORIZONS_SEC},
        "max_before": {180: max_h.get(180), 300: max_h.get(300)},
        "min_before": {180: min_h.get(180), 300: min_h.get(300)},
        "max_after": {180: max_after.get(180), 300: max_after.get(300)},
        "leak": leak,
    }


def build_fill_record(sig: dict[str, Any], path: dict[str, Any], *, window_end: float) -> dict[str, Any]:
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
        "G1_ARM_T": path.get("arm_t"),
        "G1_EVENT_T": path.get("event_t"),
        "G1_EXIT_T": path.get("event_t"),
        "G1_EXIT_BID": path.get("event_bid"),
        "G1_EXIT_PNL": path.get("event_pnl"),
        "G1_EVENT_MISS": bool(path.get("event_miss")),
        "BE_LOSS_TIME": path.get("event_t"),
        "MAX_BEFORE_180": (path.get("max_before") or {}).get(180),
        "MIN_BEFORE_180": (path.get("min_before") or {}).get(180),
        "MAX_BEFORE_300": (path.get("max_before") or {}).get(300),
        "MIN_BEFORE_300": (path.get("min_before") or {}).get(300),
        "MAX_AFTER_180": (path.get("max_after") or {}).get(180),
        "MAX_AFTER_300": (path.get("max_after") or {}).get(300),
        "PATH_QUOTE_N": int((path.get("leak") or {}).get("PATH_QUOTE_N") or 0),
        "path_leak": path.get("leak") or {},
        "passive_filled": True,
        "executable_signal": True,
        "challenger": DEVELOPMENT_CHALLENGER,
    }
    holds = path.get("holds") or {}
    hold_t = path.get("hold_t") or {}
    for h in (60, 180, 300):
        rec[f"hold_{h}"] = holds.get(h)
        rec[f"hold_t_{h}"] = hold_t.get(h)
        rec[f"markout_{h}"] = holds.get(h)
    return rec


def replay_v15_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "PLUS_BPS_ARMED_N": 0,
        "TRAIL_SEARCH_N": 0,
        "NEW_INDICATOR_N": 0,
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
                print(f"{day} v15 stream kept={events_n} last_et={last_et}", flush=True)
        views: dict[str, dict[str, np.ndarray]] = {s: bufs[s].view() for s in needed}
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
            path = walk_g1_path(views[s], fill_t=fill_t, fill_px=float(ex["fill_price"]), window_end=window_end)
            leak["PRE_FILL_QUOTE_N"] += int((path.get("leak") or {}).get("PRE_FILL_QUOTE_N") or 0)
            leak["PRE_FILL_EVENT_N"] += int((path.get("leak") or {}).get("PRE_FILL_EVENT_N") or 0)
            rec = build_fill_record(sig, path, window_end=window_end)
            if rec.get("G1_EVENT_T") is not None and rec.get("G1_EXIT_PNL") is None:
                leak["EXIT_BID_MISS_N"] += 1
            rows.append(rec)
            leak["FILL_N"] += 1
        print(f"{day} v15 kept_events={events_n} fills={len(rows)} last_et={last_et}", flush=True)
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


def save_v15_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v15_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

"""Occupancy Control causal Bid-path reconstruction. Session-close only. No 20260903."""
from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    find_capture_dir,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_redesign.branch_u_causal_harvest import replay_causal_day
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS
from research.simple_tech_redesign.exit_residual_rca_spec import RESIDUAL_CLASSES
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok
from small_paper.v1r_live_dual_lane import session_end_for_position

PATH_CACHE = RESEARCH_CACHE / "exit_residual_loss_architecture_rca"
EARLY = "EARLY_FAILURE"
GOOD = "GOOD_CONTINUATION"
DIP = "DIP_THEN_RECOVERY"
PTF = "PROFIT_THEN_FAILURE"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def residual_class(*, path_type: str, be_reached: bool) -> str:
    p = str(path_type or "OTHER")
    if p == EARLY:
        return "P_EARLY_AFTER_BE" if be_reached else "U_EARLY_NEVER_BE"
    if p == PTF:
        return "P_PROFIT_THEN_FAILURE"
    if p == DIP:
        return "PROTECTED_DIP"
    if p == GOOD:
        return "PROTECTED_GOOD"
    return "OTHER"


def walk_executable_path(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out = {
        "break_even_reached": False,
        "first_break_even_time": None,
        "be_bid": None,
        "be_yen": None,
        "peak_executable_pnl": None,
        "peak_t": None,
        "min_executable_pnl": None,
        "min_t": None,
        "last_executable_pnl": None,
        "last_t": None,
        "last_bid": None,
        "quote_n": 0,
    }
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return out
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    peak = peak_t = trough = trough_t = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _clock_ok(meta, i, ti, leak) or not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        yen = float(compute_pnl_yen_100(float(fill_px), bid, side="long"))
        out["quote_n"] = int(out["quote_n"]) + 1
        out["last_executable_pnl"] = yen
        out["last_t"] = ti
        out["last_bid"] = bid
        if peak is None or yen > float(peak):
            peak, peak_t = yen, ti
        if trough is None or yen < float(trough):
            trough, trough_t = yen, ti
        if (not out["break_even_reached"]) and yen >= 0.0:
            out["break_even_reached"] = True
            out["first_break_even_time"] = ti
            out["be_bid"] = bid
            out["be_yen"] = yen
    out["peak_executable_pnl"] = peak
    out["peak_t"] = peak_t
    out["min_executable_pnl"] = trough
    out["min_t"] = trough_t
    return out


def stream_boards(
    day: str,
    capture: Path,
    symbols: set[str],
    leak: dict[str, Any],
) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    srcs = {s: [] for s in symbols}
    clocks = {s: [] for s in symbols}
    price_ages = {s: [] for s in symbols}
    events_n = 0
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in symbols:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
        corr = corrected_board_freshness(rec, pay, float(et))
        row = _board_row(pay, float(et))
        row["fresh_sec"] = float(corr["fresh_sec"]) if _finite(corr.get("fresh_sec")) else NAN
        bufs[sym].append(row)
        srcs[sym].append(str(corr.get("source") or "UNRESOLVED"))
        clocks[sym].append(float(corr["clock_epoch"]) if _finite(corr.get("clock_epoch")) else NAN)
        price_ages[sym].append(float(corr["price_fresh_sec"]) if _finite(corr.get("price_fresh_sec")) else NAN)
        events_n += 1
        if events_n % 200000 == 0:
            print(f"{day} residual stream kept={events_n}", flush=True)
    views = {s: bufs[s].view() for s in symbols}
    metas = {
        s: {
            "source": np.asarray(srcs[s], dtype=object),
            "clock": np.asarray(clocks[s], dtype=float),
            "price_fresh": np.asarray(price_ages[s], dtype=float),
        }
        for s in symbols
    }
    return {"views": views, "metas": metas, "am_end": am_end, "events_n": events_n}


def _source_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out = {}
    for r in rows:
        t0 = _f(r.get("t0"))
        if t0 is None:
            continue
        out[(str(r.get("date") or ""), _bare(r.get("symbol")), float(t0))] = r
    return out


def attach_path(trade: dict[str, Any], src: dict[str, Any], walked: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    close_pnl = _f(trade.get("pnl_yen_100"))
    peak = _f(walked.get("peak_executable_pnl"))
    last = _f(walked.get("last_executable_pnl"))
    be = bool(walked.get("break_even_reached"))
    path_type = str(trade.get("path_type") or src.get("path_type") or "OTHER")
    cls = residual_class(path_type=path_type, be_reached=be)
    if cls not in RESIDUAL_CLASSES:
        cls = "OTHER"
    giveback = None if peak is None or close_pnl is None else max(0.0, float(peak) - float(close_pnl))
    below = None if close_pnl is None else max(0.0, -float(close_pnl))
    fill_t = _f(trade.get("fill_time") or src.get("fill_t"))
    exit_t = _f(trade.get("exit_time"))
    hold = None if fill_t is None or exit_t is None else float(exit_t) - float(fill_t)
    cache_be = dict(src.get("branch_u") or {}).get("first_break_even_time") if src else None
    cache_hit = dict(src.get("branch_u") or {}).get("break_even_reached") if src else None
    rec_be_t = _f(walked.get("first_break_even_time"))
    cache_be_t = _f(cache_be)
    if cache_hit is True and rec_be_t is not None and cache_be_t is not None:
        if abs(float(cache_be_t) - float(rec_be_t)) > 1e-12:
            leak["BE_CACHE_DRIFT_N"] = int(leak.get("BE_CACHE_DRIFT_N") or 0) + 1
    elif bool(cache_hit) != be and cache_hit is not None:
        leak["BE_CACHE_DRIFT_N"] = int(leak.get("BE_CACHE_DRIFT_N") or 0) + 1
    if close_pnl is not None and last is not None and abs(float(close_pnl) - float(last)) > 0.01:
        leak["SESSION_CLOSE_PNL_DRIFT_N"] = int(leak.get("SESSION_CLOSE_PNL_DRIFT_N") or 0) + 1
    min_yen = _f(walked.get("min_executable_pnl"))
    return {
        "date": trade.get("date"),
        "symbol": _bare(trade.get("symbol")),
        "t0": trade.get("t0"),
        "trade_id": trade.get("trade_id"),
        "fill_time": fill_t,
        "fill_price": _f(trade.get("fill_price") or src.get("fill_price")),
        "fill_role": trade.get("fill_role") or src.get("fill_role"),
        "path_type": path_type,
        "residual_class": cls,
        "exit_time": exit_t,
        "exit_price": _f(trade.get("exit_price")),
        "exit_reason": trade.get("exit_reason"),
        "session_close_pnl": close_pnl,
        "break_even_reached": be,
        "first_break_even_time": rec_be_t,
        "peak_executable_pnl": peak,
        "peak_t": walked.get("peak_t"),
        "min_executable_pnl": min_yen,
        "min_t": walked.get("min_t"),
        "peak_to_close_giveback": giveback,
        "below_be_terminal_loss": below,
        "holding_duration_sec": hold,
        "quote_n": walked.get("quote_n"),
        "positive_peak_reached": bool(peak is not None and float(peak) > 1e-12),
        "temporary_negative_excursion": None if min_yen is None else min(0.0, float(min_yen)),
        "last_executable_pnl": last,
        "session_close_match": (
            close_pnl is not None and last is not None and abs(float(close_pnl) - float(last)) <= 0.01
        ),
    }


def harvest_day(day: str, *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if day == str(today) or day in FORBIDDEN_DAYS:
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok"):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    rows = list(ubody.get("rows") or [])
    occ = replay_causal_day(rows)
    if not occ.get("control_sot_ok"):
        return {"ok": False, "blocker": f"occupancy_sot_fail:{day}", "date": day}
    ctrl = dict(occ.get("control") or {})
    trades = list(ctrl.get("trades") or [])
    leak = {
        "BE_CACHE_DRIFT_N": 0,
        "SESSION_CLOSE_PNL_DRIFT_N": 0,
        "PATH_SKIP_N": 0,
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "BOARD_CLOCK_UNRESOLVED_N": 0,
    }
    if not trades:
        body = {
            "ok": True,
            "date": day,
            "cohort": cohort,
            "spec_sha": spec_sha,
            "u_spec_sha": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "control_fill_n": 0,
            "control_sot_ok": True,
            "leftover_ok": bool(occ.get("leftover_ok")),
            "rows": [],
            "leak": leak,
            "events_n": 0,
            "control_counts": {
                "fill_n": int(ctrl.get("fill_n") or 0),
                "core_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE"),
                "added_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED"),
                "open_leftover_n": int(ctrl.get("open_leftover_n") or 0),
                "pending_leftover_n": int(ctrl.get("pending_leftover_n") or 0),
            },
        }
        PATH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    symbols = {_bare(t.get("symbol")) for t in trades if _bare(t.get("symbol"))}
    print(f"{cohort} {day} residual stream symbols={len(symbols)} control_fills={len(trades)}", flush=True)
    packed = stream_boards(day, capture, symbols, leak)
    smap = _source_map(rows)
    out_rows = []
    for t in trades:
        s = _bare(t.get("symbol"))
        t0 = _f(t.get("t0"))
        src = smap.get((str(t.get("date") or day), s, float(t0))) if t0 is not None else {}
        src = src or {}
        fill_t = _f(t.get("fill_time") or src.get("fill_t"))
        fill_px = _f(t.get("fill_price") or src.get("fill_price"))
        if fill_t is None or fill_px is None:
            leak["PATH_SKIP_N"] = int(leak.get("PATH_SKIP_N") or 0) + 1
            rec = attach_path(t, src, {}, leak)
            rec["residual_class"] = residual_class(path_type=str(t.get("path_type") or ""), be_reached=False)
            out_rows.append(rec)
            continue
        walked = walk_executable_path(
            packed["views"].get(s) or {},
            packed["metas"].get(s) or {},
            fill_t=float(fill_t),
            fill_px=float(fill_px),
            sess_end=float(packed["am_end"]),
            leak=leak,
        )
        out_rows.append(attach_path(t, src, walked, leak))
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "u_spec_sha": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
        "control_fill_n": len(out_rows),
        "control_sot_ok": True,
        "leftover_ok": bool(occ.get("leftover_ok")),
        "rows": out_rows,
        "leak": leak,
        "events_n": packed.get("events_n"),
        "control_counts": {
            "fill_n": int(ctrl.get("fill_n") or 0),
            "core_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE"),
            "added_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED"),
            "open_leftover_n": int(ctrl.get("open_leftover_n") or 0),
            "pending_leftover_n": int(ctrl.get("pending_leftover_n") or 0),
            "session_close_exit_n": int(ctrl.get("session_close_exit_n") or 0),
            "branch_u_exit_n": int(ctrl.get("branch_u_exit_n") or 0),
        },
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return body


def harvest_cohort(days: list[str], *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if any(d == str(today) or d in FORBIDDEN_DAYS for d in days):
        return {"ok": False, "blocker": "TODAY_OR_FORBIDDEN"}
    bodies = []
    rows = []
    leak_sum: dict[str, int] = {}
    fill_n = 0
    core_n = 0
    added_n = 0
    leftover_ok = True
    sot_ok = True
    for day in days:
        body = harvest_day(day, cohort=cohort, spec_sha=spec_sha, today=today)
        if not body.get("ok"):
            return body
        bodies.append(body)
        rows.extend(list(body.get("rows") or []))
        cc = dict(body.get("control_counts") or {})
        fill_n += int(cc.get("fill_n") or 0)
        core_n += int(cc.get("core_fill_n") or 0)
        added_n += int(cc.get("added_fill_n") or 0)
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        sot_ok = sot_ok and bool(body.get("control_sot_ok"))
        for k, v in dict(body.get("leak") or {}).items():
            leak_sum[k] = int(leak_sum.get(k) or 0) + int(v or 0)
    pnl = sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in rows)
    return {
        "ok": True,
        "cohort": cohort,
        "days": list(days),
        "rows": rows,
        "day_bodies": bodies,
        "leak": leak_sum,
        "control_fill_n": fill_n,
        "control_core_n": core_n,
        "control_added_n": added_n,
        "control_total_pnl": pnl,
        "occupancy_sot_ok": sot_ok,
        "leftover_ok": leftover_ok,
        "spec_sha": spec_sha,
    }

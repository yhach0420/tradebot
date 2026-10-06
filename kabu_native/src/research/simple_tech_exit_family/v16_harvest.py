"""V16 sealed Bid1 ARM + completed 1m Close G2. Clock = fill_time. No EMA/RCI/BB. No EXIT rule."""
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
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_exit_family.isolation import RESEARCH_CACHE
from research.simple_tech_exit_family.v14_harvest import _bps, _e4, _valid_exit_bid, first_exit_bid
from research.simple_tech_exit_family.v15_harvest import walk_g1_path
from research.simple_tech_exit_family.v16_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    FILL_EVIDENCE,
    PATH_WINDOW_SEC,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V16_CACHE = RESEARCH_CACHE / "v16_bar_confirmed_be_reloss"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def detect_g2(
    bars: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    arm_t: float,
    sess_end: float,
) -> Optional[dict[str, Any]]:
    close = bars.get("close")
    fin = bars.get("finalize_t")
    n = int(close.size) if close is not None else 0
    if close is None or fin is None or n == 0 or not _finite(arm_t):
        return None
    for i in range(n):
        et = float(fin[i]) if _finite(fin[i]) else None
        if et is None:
            continue
        if et + 1e-12 < float(fill_t):
            continue
        if et <= float(arm_t) + 1e-12:
            continue
        if et > float(sess_end) + 1e-12:
            continue
        cl = float(close[i]) if _finite(close[i]) else None
        if cl is None or cl > float(fill_px) + 1e-12:
            continue
        return {
            "event_t": et,
            "bar_minute": float(bars["minute_epoch"][i]) if _finite(bars["minute_epoch"][i]) else None,
            "bar_close": cl,
            **first_exit_bid(board, event_t=et, fill_px=fill_px, sess_end=sess_end),
        }
    return None


def max_after_event(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    event_t: float,
    window_end: float,
) -> dict[int, Optional[float]]:
    t = board.get("t")
    out: dict[int, Optional[float]] = {180: None, 300: None}
    if t is None or int(t.size) == 0:
        return out
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(event_t) + 1e-12:
            continue
        if ti > float(window_end) + 1e-12:
            break
        if not _valid_exit_bid(board, i):
            continue
        mk = _bps(board["bid"][i], fill_px)
        if mk is None:
            continue
        dt = ti - float(fill_t)
        for h in (180, 300):
            if dt <= float(h) + 1e-12:
                out[h] = float(mk) if out[h] is None else max(float(out[h]), float(mk))
    return out


def build_fill_record(
    sig: dict[str, Any],
    path: dict[str, Any],
    g2: Optional[dict[str, Any]],
    max_after: dict[int, Optional[float]],
    *,
    window_end: float,
) -> dict[str, Any]:
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
        "FIRST_POSITIVE_TIME": path.get("first_pos_t"),
        "G2_ARM_T": path.get("arm_t"),
        "G2_EVENT_T": (g2 or {}).get("event_t"),
        "G2_BAR_MINUTE": (g2 or {}).get("bar_minute"),
        "G2_BAR_CLOSE": (g2 or {}).get("bar_close"),
        "G2_EXIT_T": (g2 or {}).get("exit_t"),
        "G2_EXIT_BID": (g2 or {}).get("exit_bid"),
        "G2_EXIT_PNL": (g2 or {}).get("exit_pnl"),
        "G2_EVENT_MISS": bool(g2 is not None and (g2 or {}).get("miss")),
        "MAX_BEFORE_180": (path.get("max_before") or {}).get(180),
        "MIN_BEFORE_180": (path.get("min_before") or {}).get(180),
        "MAX_BEFORE_300": (path.get("max_before") or {}).get(300),
        "MIN_BEFORE_300": (path.get("min_before") or {}).get(300),
        "MAX_AFTER_180": max_after.get(180),
        "MAX_AFTER_300": max_after.get(300),
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


def replay_v16_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "TWO_BARS_N": 0,
        "NEW_INDICATOR_N": 0,
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
                print(f"{day} v16 stream kept={events_n} last_et={last_et}", flush=True)
        views: dict[str, dict[str, np.ndarray]] = {}
        bars: dict[str, dict[str, np.ndarray]] = {}
        for s in needed:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            if not integ.get("ok"):
                leak["BAR_INTEG_FAIL_N"] += 1
            views[s] = bufs[s].view()
            bars[s] = raw
        rows = []
        for sig in fills:
            ex = _e4(sig)
            if not ex.get("filled") or not _finite(ex.get("fill_t")) or not _finite(ex.get("fill_price")):
                leak["UNFILLED_VIRTUAL_N"] += 1
                continue
            fill_t = float(ex["fill_t"])
            fill_px = float(ex["fill_price"])
            t0 = float(sig.get("t0") or 0.0)
            if fill_t + 1e-12 < t0 or fill_t > t0 + float(E4_WAIT_BUDGET_SEC) + 1e-12:
                leak["FUTURE_QUOTE_N"] += 1
            s = _bare(sig.get("symbol"))
            window_end = min(fill_t + float(PATH_WINDOW_SEC), float(am_end))
            path = walk_g1_path(views[s], fill_t=fill_t, fill_px=fill_px, window_end=window_end)
            leak["PRE_FILL_QUOTE_N"] += int((path.get("leak") or {}).get("PRE_FILL_QUOTE_N") or 0)
            g2 = None
            max_after: dict[int, Optional[float]] = {180: None, 300: None}
            if _finite(path.get("arm_t")):
                g2 = detect_g2(
                    bars[s],
                    views[s],
                    fill_t=fill_t,
                    fill_px=fill_px,
                    arm_t=float(path["arm_t"]),
                    sess_end=float(am_end),
                )
            if g2:
                if float(g2["event_t"]) + 1e-12 < fill_t:
                    leak["PRE_FILL_EVENT_N"] += 1
                if g2.get("miss"):
                    leak["EXIT_BID_MISS_N"] += 1
                max_after = max_after_event(
                    views[s],
                    fill_t=fill_t,
                    fill_px=fill_px,
                    event_t=float(g2["event_t"]),
                    window_end=window_end,
                )
            rows.append(build_fill_record(sig, path, g2, max_after, window_end=window_end))
            leak["FILL_N"] += 1
        print(f"{day} v16 kept_events={events_n} fills={len(rows)} last_et={last_et}", flush=True)
        del bufs, builders, views, bars
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


def save_v16_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v16_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

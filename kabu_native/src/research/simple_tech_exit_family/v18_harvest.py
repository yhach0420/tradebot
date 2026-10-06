"""V18 sealed first causal Bid1 after fill+180 (or AM close). No bars. No dynamic EXIT. No horizon search."""
from __future__ import annotations

import gc
import os
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
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
from research.simple_tech_exit_family.v14_harvest import _bps, _e4, _valid_exit_bid, first_exit_bid
from research.simple_tech_exit_family.v18_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    FILL_EVIDENCE,
    HOLD_SEC,
    SHARES,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V18_CACHE = RESEARCH_CACHE / "v18_fixed180_exit"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def last_before_horizon(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    horizon_sec: float,
) -> dict[str, Any]:
    """V14 HOLD-style last executable Bid with dt <= horizon. Reference only. Not EXIT_PRICE."""
    t = board.get("t")
    out = {"t": None, "bid": None, "bps": None}
    if t is None or int(t.size) == 0:
        return out
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    last_t = last_bid = last_bps = None
    end = float(fill_t) + float(horizon_sec)
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > end + 1e-12:
            break
        if not _valid_exit_bid(board, i):
            continue
        mk = _bps(board["bid"][i], fill_px)
        if mk is None:
            continue
        last_t, last_bid, last_bps = ti, float(board["bid"][i]), float(mk)
    return {"t": last_t, "bid": last_bid, "bps": last_bps}


def build_fill_record(
    sig: dict[str, Any],
    *,
    scheduled_t: float,
    session_clamped: bool,
    exit_pack: dict[str, Any],
    hold_recompute: dict[str, Any],
    sess_end: float,
) -> dict[str, Any]:
    ex = _e4(sig)
    fill_t = float(ex["fill_t"])
    fill_px = float(ex["fill_price"])
    exit_t = exit_pack.get("exit_t")
    exit_bid = exit_pack.get("exit_bid")
    miss = bool(exit_pack.get("miss"))
    latency = (float(exit_t) - float(scheduled_t)) if (not miss and _finite(exit_t)) else None
    yen = None
    if (not miss) and _finite(exit_bid):
        yen = float(compute_pnl_yen_100(fill_px, float(exit_bid), side="long"))
    pre_decision = bool((not miss) and _finite(exit_t) and float(exit_t) + 1e-12 < float(scheduled_t))
    used_last_before = False
    if (not miss) and _finite(exit_t) and _finite(hold_recompute.get("t")):
        used_last_before = abs(float(exit_t) - float(hold_recompute["t"])) <= 1e-9 and float(exit_t) + 1e-12 < float(
            scheduled_t
        )
    rec = {
        "date": sig.get("date"),
        "symbol": str(sig.get("symbol") or "").replace(".T", ""),
        "t0": float(sig.get("t0") or 0.0),
        "fill_t": fill_t,
        "fill_price": fill_px,
        "limit_price": ex.get("limit_price"),
        "inside_collapsed": bool(ex.get("collapsed_to_bid") or sig.get("inside_collapsed_to_bid")),
        "wait_sec": (fill_t - float(sig["t0"])) if _finite(sig.get("t0")) else None,
        "evidence": ex.get("evidence") or FILL_EVIDENCE,
        "challenger": DEVELOPMENT_CHALLENGER,
        "sess_end": float(sess_end),
        "scheduled_exit_time": float(scheduled_t),
        "session_clamped": bool(session_clamped),
        "actual_exit_quote_time": exit_t,
        "exit_latency_sec": latency,
        "exit_bid": exit_bid,
        "exit_bps": exit_pack.get("exit_pnl"),
        "pnl_yen_100": yen,
        "shares": int(SHARES),
        "EXIT_MISS": bool(miss),
        "PRE_DECISION_EXIT": bool(pre_decision),
        "LAST_BEFORE_USED_AS_EXIT": bool(used_last_before),
        "HOLD180_RECOMPUTE_T": hold_recompute.get("t"),
        "HOLD180_RECOMPUTE_BPS": hold_recompute.get("bps"),
        "V14_HOLD180": None,
        "passive_filled": True,
        "executable_signal": True,
    }
    rec["markout_180"] = rec.get("exit_bps")
    return rec


def replay_v18_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "THRESHOLD_SEARCH_N": 0,
        "ALT_HOLD_N": 0,
        "G1_N": 0,
        "G2_N": 0,
        "G3_N": 0,
        "D1_N": 0,
        "D2_N": 0,
        "D3_N": 0,
        "PASSIVE_ASK_N": 0,
        "INSIDE_SELL_N": 0,
        "WAIT_REPRICE_CHASE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "LAST_BEFORE_USED_AS_EXIT_N": 0,
        "NEW_INDICATOR_N": 0,
        "EXIT_BID_MISS_N": 0,
        "SESSION_CLAMP_N": 0,
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
                print(f"{day} v18 stream kept={events_n} last_et={last_et}", flush=True)
        views: dict[str, dict[str, np.ndarray]] = {s: bufs[s].view() for s in needed}
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
            raw_sched = float(fill_t) + float(HOLD_SEC)
            session_clamped = raw_sched > float(am_end) + 1e-12
            scheduled_t = min(raw_sched, float(am_end))
            if session_clamped:
                leak["SESSION_CLAMP_N"] += 1
            hold_recompute = last_before_horizon(
                views[s], fill_t=fill_t, fill_px=fill_px, horizon_sec=float(HOLD_SEC)
            )
            exit_pack = first_exit_bid(views[s], event_t=scheduled_t, fill_px=fill_px, sess_end=float(am_end))
            rec = build_fill_record(
                sig,
                scheduled_t=scheduled_t,
                session_clamped=session_clamped,
                exit_pack=exit_pack,
                hold_recompute=hold_recompute,
                sess_end=float(am_end),
            )
            if rec.get("EXIT_MISS"):
                leak["EXIT_BID_MISS_N"] += 1
            if rec.get("PRE_DECISION_EXIT"):
                leak["PRE_DECISION_EXIT_N"] += 1
            if rec.get("LAST_BEFORE_USED_AS_EXIT"):
                leak["LAST_BEFORE_USED_AS_EXIT_N"] += 1
            if _finite(rec.get("actual_exit_quote_time")) and float(rec["actual_exit_quote_time"]) + 1e-12 < fill_t:
                leak["PRE_FILL_EVENT_N"] += 1
            rows.append(rec)
            leak["FILL_N"] += 1
        print(f"{day} v18 kept_events={events_n} fills={len(rows)} last_et={last_et}", flush=True)
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


def save_v18_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v18_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

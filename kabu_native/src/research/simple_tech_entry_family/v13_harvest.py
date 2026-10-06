"""V13 E4-only sealed replay. Unchanged V12 fill engine. No E1-E6 ranking. No EXIT. No V12 writes."""
from __future__ import annotations

import gc
import os
import time
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.v11_harvest import quote_row
from research.simple_tech_entry_family.v12_harvest import _attach_markouts, _empty_arm, _finite, improve_1tick_limit
from research.simple_tech_entry_family.v12_spec import FILL_EVIDENCE
from research.simple_tech_entry_family.v13_spec import DEVELOPMENT_CHALLENGER, E4_WAIT_BUDGET_SEC, V12_SPEC_SHA256_EXPECTED
from small_paper.v1r_live_dual_lane import session_end_for_position

V13_CACHE = CACHE / "v13_entry_execution_structure_verification"


def simulate_e4_only(rec: dict[str, Any], board: dict, *, am_end: float, leak: dict[str, Any]) -> dict[str, Any]:
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
    if collapsed:
        leak["INSIDE_COLLAPSE_N"] = int(leak.get("INSIDE_COLLAPSE_N") or 0) + 1
    if inside_lim >= ask0 - 1e-12 and abs(inside_lim - bid0) > 1e-12:
        leak["ASK_CROSS_LIMIT_N"] = int(leak.get("ASK_CROSS_LIMIT_N") or 0) + 1
    rec["inside_collapsed_to_bid"] = bool(collapsed)
    rec["inside_limit"] = float(inside_lim)
    rec["bid_limit"] = float(bid0)
    rec["tick"] = float(tick)
    fill = standalone_fill(
        board,
        t0=float(t0),
        wait_sec=float(E4_WAIT_BUDGET_SEC),
        limit_price=float(inside_lim),
        sess_end=float(am_end),
    )
    filled = bool(fill.get("WOULD_FILL"))
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
        "fill_reason": None if filled else fill.get("fill_reason"),
        "nonfill_class": None if filled else fill.get("nonfill_class"),
    }
    if filled:
        leak["ASK_CROSS_FILL_N"] = int(leak.get("ASK_CROSS_FILL_N") or 0) + 1
        if not collapsed:
            leak["INSIDE_ASK_CROSS_FILL_N"] = int(leak.get("INSIDE_ASK_CROSS_FILL_N") or 0) + 1
    _attach_markouts(arm, rec, board, am_end=am_end)
    rec["exec"] = {DEVELOPMENT_CHALLENGER: arm}
    return rec


def replay_e4_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "EXIT_SIM_N": 0,
        "PROFIT_RANKING_N": 0,
        "EXTRA_WAIT_ARM_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "FALLBACK_MARKET_N": 0,
        "OPTIMISTIC_FILL_N": 0,
        "TOUCH_FILL_N": 0,
        "QUEUE_FILL_N": 0,
        "FUTURE_BOARD_N": 0,
        "NOT_ELIGIBLE_N": 0,
        "INSIDE_COLLAPSE_N": 0,
        "ASK_CROSS_LIMIT_N": 0,
        "ASK_CROSS_FILL_N": 0,
        "INSIDE_ASK_CROSS_FILL_N": 0,
        "NO_ASK0_N": 0,
        "MID0_MISS_N": 0,
        "FULL_MISS_N": 0,
        "GROSS_MISS_N": 0,
        "DUAL_BID_NE_EXEC_BID_N": 0,
    }
    if not signals:
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
                print(f"{day} v13 e4 stream kept={events_n} last_et={last_et}", flush=True)
        rows = []
        views = {s: bufs[s].view() for s in needed}
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rec = quote_row(sig, views[s], am_end=am_end, leak=leak)
            rows.append(simulate_e4_only(rec, views[s], am_end=am_end, leak=leak))
        print(f"{day} v13 e4 kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
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


def save_v13_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v13_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body

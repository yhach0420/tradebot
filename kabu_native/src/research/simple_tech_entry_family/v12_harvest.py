"""V12 quote + canonical ask-cross fill replay on frozen B1. No reprice. No fallback market. No EXIT."""
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
from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.e1_x34a_execution_policy.arms import inside_limit_price
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row
from research.simple_tech_entry_family.v3_harvest import _last_bid_before
from research.simple_tech_entry_family.v11_harvest import _last_dual_before, quote_row
from research.simple_tech_entry_family.v12_spec import (
    CONTROL_ARM,
    FILL_EVIDENCE,
    MARKOUT_HORIZONS_SEC,
    POLICY_ARMS,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V12_CACHE = CACHE / "v12_entry_execution"


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _bps(num: Any, den: Any) -> Optional[float]:
    if not _finite(num) or not _finite(den) or float(den) <= 0.0:
        return None
    return (float(num) / float(den) - 1.0) * 10000.0


def _improve_vs_ask(ask0: Any, fill: Any) -> Optional[float]:
    if not _finite(ask0) or not _finite(fill) or float(ask0) <= 0.0:
        return None
    return (float(ask0) - float(fill)) / float(ask0) * 10000.0


def improve_1tick_limit(bid0: float, ask0: float) -> tuple[float, bool, float]:
    ins = inside_limit_price(float(bid0), float(ask0))
    tick = float(ins.get("tick") or jpx_tick_size_yen(float(bid0)))
    if ins.get("ok") and ins.get("limit") is not None:
        lim = float(ins["limit"])
        if lim < float(ask0) - 1e-12:
            return lim, False, tick
    return float(bid0), True, tick


def _attach_markouts(arm: dict[str, Any], rec: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float) -> None:
    t0 = float(rec["t0"])
    fp = arm.get("fill_price")
    ft = arm.get("fill_t")
    ask0 = rec.get("ask0")
    mid0 = rec.get("mid0")
    arm["improve_vs_ask0_bps"] = _improve_vs_ask(ask0, fp) if arm.get("filled") else None
    arm["entry_vs_mid0_bps"] = _bps(fp, mid0) if arm.get("filled") else None
    if not arm.get("filled") or not _finite(fp) or not _finite(ft):
        for h in MARKOUT_HORIZONS_SEC:
            hid = int(h)
            arm[f"prim_mid_{hid}"] = None
            arm[f"sec_bid_{hid}"] = None
            arm[f"ft_mid_{hid}"] = None
        return
    for h in MARKOUT_HORIZONS_SEC:
        hid = int(h)
        mark_t = min(float(t0) + float(h), float(am_end))
        dual = _last_dual_before(board, t0=t0, mark_t=mark_t)
        bid_exec, _bt = _last_bid_before(board, t0=t0, mark_t=mark_t)
        arm[f"prim_mid_{hid}"] = _bps(dual.get("mid"), fp)
        arm[f"sec_bid_{hid}"] = _bps(bid_exec, fp)
        mark_f = min(float(ft) + float(h), float(am_end))
        dual_f = _last_dual_before(board, t0=float(ft), mark_t=mark_f)
        arm[f"ft_mid_{hid}"] = _bps(dual_f.get("mid"), fp)


def _empty_arm(family: str, wait_sec: float, limit_price: Any, *, collapsed: bool) -> dict[str, Any]:
    arm: dict[str, Any] = {
        "filled": False,
        "fill_price": None,
        "fill_t": None,
        "limit_price": limit_price,
        "wait_budget_sec": float(wait_sec),
        "waited_sec": None,
        "family": family,
        "collapsed_to_bid": bool(collapsed),
        "evidence": None,
        "fill_reason": "NOT_ELIGIBLE",
        "nonfill_class": "NOT_ELIGIBLE",
        "improve_vs_ask0_bps": None,
        "entry_vs_mid0_bps": None,
    }
    for h in MARKOUT_HORIZONS_SEC:
        hid = int(h)
        arm[f"prim_mid_{hid}"] = None
        arm[f"sec_bid_{hid}"] = None
        arm[f"ft_mid_{hid}"] = None
    return arm


def simulate_arms(rec: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float, leak: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not rec.get("executable_signal") or not _finite(rec.get("bid0")) or not _finite(rec.get("ask0")):
        leak["NOT_ELIGIBLE_N"] = int(leak.get("NOT_ELIGIBLE_N") or 0) + 1
        out[CONTROL_ARM] = _empty_arm("CROSS", 0.0, None, collapsed=False)
        for aid, fam, wait in POLICY_ARMS:
            out[aid] = _empty_arm(str(fam), float(wait), None, collapsed=False)
        rec["inside_collapsed_to_bid"] = None
        rec["inside_limit"] = None
        rec["bid_limit"] = None
        rec["tick"] = None
        return out
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

    e0 = {
        "filled": True,
        "fill_price": float(ask0),
        "fill_t": float(t0),
        "limit_price": float(ask0),
        "wait_budget_sec": 0.0,
        "waited_sec": 0.0,
        "family": "CROSS",
        "collapsed_to_bid": False,
        "evidence": "AGGRESSIVE_ASK",
        "fill_reason": None,
        "nonfill_class": None,
    }
    _attach_markouts(e0, rec, board, am_end=am_end)
    out[CONTROL_ARM] = e0
    leak["E0_FILL_N"] = int(leak.get("E0_FILL_N") or 0) + 1

    for aid, fam, wait in POLICY_ARMS:
        lim = float(bid0) if str(fam) == "PASSIVE_BID" else float(inside_lim)
        if lim >= float(ask0) - 1e-12 and str(fam) == "IMPROVE_1TICK" and not collapsed:
            leak["ASK_CROSS_LIMIT_N"] = int(leak.get("ASK_CROSS_LIMIT_N") or 0) + 1
        fill = standalone_fill(
            board,
            t0=float(t0),
            wait_sec=float(wait),
            limit_price=float(lim),
            sess_end=float(am_end),
        )
        filled = bool(fill.get("WOULD_FILL"))
        arm = {
            "filled": filled,
            "fill_price": fill.get("fill_price") if filled else None,
            "fill_t": fill.get("fill_t") if filled else None,
            "limit_price": float(lim),
            "wait_budget_sec": float(wait),
            "waited_sec": (float(fill["fill_t"]) - float(t0)) if filled and _finite(fill.get("fill_t")) else None,
            "family": str(fam),
            "collapsed_to_bid": bool(collapsed) if str(fam) == "IMPROVE_1TICK" else False,
            "evidence": FILL_EVIDENCE if filled else None,
            "fill_reason": None if filled else fill.get("fill_reason"),
            "nonfill_class": None if filled else fill.get("nonfill_class"),
        }
        if filled:
            leak["ASK_CROSS_FILL_N"] = int(leak.get("ASK_CROSS_FILL_N") or 0) + 1
            if str(fam) == "IMPROVE_1TICK" and not collapsed:
                leak["INSIDE_ASK_CROSS_FILL_N"] = int(leak.get("INSIDE_ASK_CROSS_FILL_N") or 0) + 1
        _attach_markouts(arm, rec, board, am_end=am_end)
        out[aid] = arm
    return out


def execution_row(sig: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float, leak: dict[str, Any]) -> dict[str, Any]:
    rec = quote_row(sig, board, am_end=am_end, leak=leak)
    rec["exec"] = simulate_arms(rec, board, am_end=am_end, leak=leak)
    return rec


def process_v12_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "SIGNAL_RECAPTURE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "INVERSE_BOARD_GATE_N": 0,
        "BOARD_HARD_VETO_N": 0,
        "PA_RESTORE_N": 0,
        "VOLUME_RESTORE_N": 0,
        "SPREAD_GATE_N": 0,
        "EXTRA_WAIT_ARM_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "FALLBACK_MARKET_N": 0,
        "OPTIMISTIC_FILL_N": 0,
        "TOUCH_FILL_N": 0,
        "QUEUE_FILL_N": 0,
        "FUTURE_BOARD_N": 0,
        "FUTURE_ASK_USE_N": 0,
        "MIDPOINT_ENTRY_N": 0,
        "NO_ASK0_N": 0,
        "MID0_MISS_N": 0,
        "FULL_MISS_N": 0,
        "GROSS_MISS_N": 0,
        "DUAL_BID_NE_EXEC_BID_N": 0,
        "NOT_ELIGIBLE_N": 0,
        "INSIDE_COLLAPSE_N": 0,
        "ASK_CROSS_LIMIT_N": 0,
        "E0_FILL_N": 0,
        "ASK_CROSS_FILL_N": 0,
        "INSIDE_ASK_CROSS_FILL_N": 0,
    }
    if not signals:
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
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
                print(f"{day} v12 stream kept={events_n} last_et={last_et}", flush=True)
        rows = []
        views = {s: bufs[s].view() for s in needed}
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rows.append(execution_row(sig, views[s], am_end=am_end, leak=leak))
        print(f"{day} v12 kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
        del bufs, views
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
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


def save_v12_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")

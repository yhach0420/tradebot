"""V24 sealed Capture restream: Bid/Ask board freshness, frozen E4. No CurrentPriceTime board clock. No PnL."""
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
from research.simple_tech_entry_family.harvest import (
    _Buf,
    _board_row,
    _f,
    _parse_iso,
    _snap_at,
    load_day_cache,
)
from research.simple_tech_entry_family.v11_harvest import quote_row
from research.simple_tech_entry_family.v13_harvest import simulate_e4_only
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_harvest import _slim_e4, classify_uneval
from research.simple_tech_redesign.v22_spec import UNEVAL_CLASSES, V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v23_spec import V22_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_spec import DEVELOPMENT_CHALLENGER, E4_WAIT_BUDGET_SEC
from small_paper.v1r_live_dual_lane import session_end_for_position

V22_CACHE = RESEARCH_CACHE / "v22_entry_coverage_rca"
V24_CACHE = RESEARCH_CACHE / "v24_board_freshness_semantics_correction_replay"
INGRESS_KEYS = ("received_at", "received_at_jst", "persisted_at", "received_at_utc")
NAN = float("nan")


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _ingress_epoch(rec: dict[str, Any], pay: dict[str, Any]) -> Optional[float]:
    for obj in (rec, pay):
        if not isinstance(obj, dict):
            continue
        for k in INGRESS_KEYS:
            t = _parse_iso(obj.get(k))
            if t is not None:
                return float(t)
    return None


def _board_quote_t(pay: dict[str, Any]) -> Optional[float]:
    cands: list[float] = []
    for k in ("BidTime", "AskTime"):
        t = _parse_iso(pay.get(k))
        if t is not None:
            cands.append(float(t))
    for nest_k in ("Buy1", "Sell1"):
        nest = pay.get(nest_k)
        if not isinstance(nest, dict):
            continue
        for k in ("Time", "QuoteTime", "BidTime", "AskTime"):
            t = _parse_iso(nest.get(k))
            if t is not None:
                cands.append(float(t))
    if not cands:
        return None
    return max(cands)


def corrected_board_freshness(
    rec: dict[str, Any],
    pay: dict[str, Any],
    event_t: float,
) -> dict[str, Any]:
    t_board = _board_quote_t(pay)
    t_ing = _ingress_epoch(rec, pay)
    t_price = _parse_iso(pay.get("CurrentPriceTime"))
    price_fresh = (float(event_t) - float(t_price)) if t_price is not None else None
    et = float(event_t)
    source = "UNRESOLVED"
    clock = None
    if t_board is not None and float(t_board) <= et + 1e-12:
        clock = float(t_board)
        source = "BOARD_EVENT_TIME"
    elif t_ing is not None and float(t_ing) <= et + 1e-12:
        clock = float(t_ing)
        source = "INGRESS_RECEIVED_AT"
    if clock is None:
        return {
            "fresh_sec": NAN,
            "source": "UNRESOLVED",
            "clock_epoch": None,
            "price_fresh_sec": price_fresh,
            "board_event_after_event_t": bool(t_board is not None and float(t_board) > et + 1e-12),
        }
    age = et - float(clock)
    if age < 0.0:
        age = 0.0
    return {
        "fresh_sec": float(age),
        "source": source,
        "clock_epoch": float(clock),
        "price_fresh_sec": price_fresh,
        "board_event_after_event_t": bool(t_board is not None and float(t_board) > et + 1e-12),
    }


def _former_stale(sig: dict[str, Any]) -> bool:
    if str(sig.get("cohort") or "") != "C":
        return False
    if str(sig.get("uneval_class") or "") == "stale":
        return True
    return str(sig.get("ask_reason") or "") == "STALE"


def corrected_row(
    sig: dict[str, Any],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec = quote_row(sig, board, am_end=am_end, leak=leak)
    t0 = float(rec["t0"])
    snap = _snap_at(board, t0)
    rec["board_state"] = str(snap.get("state") or "") if snap.get("ok") else ""
    rec["v22_cohort"] = str(sig.get("cohort") or "")
    rec["v22_executable"] = bool(sig.get("executable_signal"))
    rec["v22_e4"] = dict(sig.get("e4") or {})
    rec["v22_ask_reason"] = str(sig.get("ask_reason") or "")
    rec["former_stale"] = _former_stale(sig)
    rec["PRICE_FRESHNESS_SEC"] = None
    rec["BOARD_FRESHNESS_CLOCK_SOURCE"] = "UNRESOLVED"
    rec["BOARD_FRESHNESS_CLOCK_EPOCH"] = None
    rec["board_fresh_sec"] = snap.get("fresh_sec") if snap.get("ok") else None
    if snap.get("ok") and _finite(snap.get("t")) and float(snap["t"]) > t0 + 1e-12:
        leak["FUTURE_BOARD_CARRYBACK_N"] = int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0) + 1
        leak["FUTURE_BOARD_N"] = int(leak.get("FUTURE_BOARD_N") or 0) + 1
    if snap.get("ok"):
        i = int(snap["i"])
        rec["BOARD_FRESHNESS_CLOCK_SOURCE"] = str(meta["source"][i] or "UNRESOLVED")
        clk = float(meta["clock"][i]) if _finite(meta["clock"][i]) else None
        rec["BOARD_FRESHNESS_CLOCK_EPOCH"] = clk
        rec["PRICE_FRESHNESS_SEC"] = float(meta["price_fresh"][i]) if _finite(meta["price_fresh"][i]) else None
        rec["board_fresh_sec"] = float(snap["fresh_sec"]) if _finite(snap.get("fresh_sec")) else None
        if clk is not None and float(clk) > t0 + 1e-12:
            leak["FUTURE_TIMESTAMP_CARRYBACK_N"] = int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0) + 1
        if rec["BOARD_FRESHNESS_CLOCK_SOURCE"] == "UNRESOLVED":
            leak["BOARD_CLOCK_UNRESOLVED_N"] = int(leak.get("BOARD_CLOCK_UNRESOLVED_N") or 0) + 1
        if rec["BOARD_FRESHNESS_CLOCK_SOURCE"] not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED"):
            leak["CURRENT_PRICE_TIME_AS_BOARD_FRESH_N"] = int(leak.get("CURRENT_PRICE_TIME_AS_BOARD_FRESH_N") or 0) + 1
    else:
        leak["BOARD_CLOCK_UNRESOLVED_N"] = int(leak.get("BOARD_CLOCK_UNRESOLVED_N") or 0) + 1
    if rec.get("executable_signal"):
        rec["uneval_class"] = None
        rec["funnel_reason"] = "EXECUTION_EVALUABLE"
    else:
        rec["uneval_class"] = classify_uneval(snap, str(rec.get("ask_reason") or ""))
        rec["funnel_reason"] = f"UNEVALUABLE_{rec['uneval_class']}"
        if rec["uneval_class"] not in UNEVAL_CLASSES:
            rec["uneval_class"] = "other"
            rec["funnel_reason"] = "UNEVALUABLE_other"
    rec = simulate_e4_only(rec, board, am_end=am_end, leak=leak)
    rec["e4"] = _slim_e4(rec)
    rec["e4_filled"] = bool(rec["e4"].get("filled"))
    rec.pop("exec", None)
    if rec.get("executable_signal"):
        if rec["e4_filled"]:
            rec["cohort"] = "A"
            rec["funnel_reason"] = "E4_FILLED"
        else:
            rec["cohort"] = "B"
            nf = str(rec["e4"].get("nonfill_class") or rec["e4"].get("fill_reason") or "E4_NONFILL")
            rec["funnel_reason"] = f"E4_NONFILL_{nf}"
    else:
        rec["cohort"] = "C"
        rec["e4_filled"] = False
    rec["PATH_USED_FOR_DECISION"] = False
    rec["PNL_EVAL"] = False
    rec["VIRTUAL_FILL"] = False
    return rec


def replay_corrected_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "VIRTUAL_FILL_N": 0,
        "FILL_REPLAY_ON_FUTURE_QUOTE_N": 0,
        "PNL_EVAL_N": 0,
        "FIXED180_PNL_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "FRESHNESS_CHANGE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "E4_CHANGE_N": 0,
        "FUTURE_BOARD_N": 0,
        "FUTURE_BOARD_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "BOARD_CLOCK_UNRESOLVED_N": 0,
        "BOARD_EVENT_AFTER_EVENT_T_N": 0,
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
        "EXIT_SIM_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "FALLBACK_MARKET_N": 0,
        "OPTIMISTIC_FILL_N": 0,
        "TOUCH_FILL_N": 0,
        "QUEUE_FILL_N": 0,
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
        srcs: dict[str, list[str]] = {s: [] for s in needed}
        clocks: dict[str, list[float]] = {s: [] for s in needed}
        price_ages: dict[str, list[float]] = {s: [] for s in needed}
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
            corr = corrected_board_freshness(rec, pay, float(et))
            if corr.get("board_event_after_event_t"):
                leak["BOARD_EVENT_AFTER_EVENT_T_N"] = int(leak["BOARD_EVENT_AFTER_EVENT_T_N"]) + 1
            row = _board_row(pay, float(et))
            row["fresh_sec"] = float(corr["fresh_sec"]) if _finite(corr.get("fresh_sec")) else NAN
            bufs[sym].append(row)
            srcs[sym].append(str(corr.get("source") or "UNRESOLVED"))
            clocks[sym].append(float(corr["clock_epoch"]) if _finite(corr.get("clock_epoch")) else NAN)
            price_ages[sym].append(float(corr["price_fresh_sec"]) if _finite(corr.get("price_fresh_sec")) else NAN)
            last_et = float(et)
            events_n += 1
            if events_n % 200000 == 0:
                print(f"{day} v24 corrected stream kept={events_n} last_et={last_et}", flush=True)
        views = {s: bufs[s].view() for s in needed}
        metas = {
            s: {
                "source": np.asarray(srcs[s], dtype=object),
                "clock": np.asarray(clocks[s], dtype=float),
                "price_fresh": np.asarray(price_ages[s], dtype=float),
            }
            for s in needed
        }
        rows = []
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rows.append(
                corrected_row(
                    sig,
                    views[s],
                    metas[s],
                    am_end=am_end,
                    leak=leak,
                )
            )
        print(f"{day} v24 corrected kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
        del bufs, views, metas, srcs, clocks, price_ages
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
            "signal_n": len(rows),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def load_v22_day_rows(day: str) -> list[dict[str, Any]]:
    body = load_day_cache(V22_CACHE / f"day_{day}.json", V22_SPEC_SHA256_EXPECTED)
    if not body:
        return []
    return list(body.get("rows") or [])


def save_v24_day_cache(path: Path, body: dict[str, Any]) -> None:
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
            "signal_n": body.get("signal_n"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v24_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert DEVELOPMENT_CHALLENGER == "E4_INSIDE1_W5"

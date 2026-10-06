"""V25 independent sealed replay of V24 corrected board freshness. Extra t0 clock audit fields. No PnL."""
from __future__ import annotations

import gc
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.harvest import _Buf, _board_row, _parse_iso, _snap_at, load_day_cache
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import (
    NAN,
    _board_quote_t,
    _finite,
    _ingress_epoch,
    corrected_board_freshness,
    corrected_row,
    load_v22_day_rows,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
V25_CACHE = RESEARCH_CACHE / "v25_corrected_execution_baseline_reconciliation"


def _iso(t: Any) -> Optional[str]:
    if not _finite(t):
        return None
    return datetime.fromtimestamp(float(t), JST).isoformat()


def _age_t0(t0: float, earlier: Any) -> Optional[float]:
    if not _finite(earlier):
        return None
    return float(t0) - float(earlier)


def attach_t0_clocks(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    extra: dict[str, np.ndarray],
    *,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t0 = float(rec["t0"])
    snap = _snap_at(board, t0)
    rec["BOARD_CLOCK_SOURCE"] = rec.get("BOARD_FRESHNESS_CLOCK_SOURCE") or "UNRESOLVED"
    rec["board_timestamp_epoch"] = None
    rec["price_timestamp_epoch"] = None
    rec["ingress_timestamp_epoch"] = None
    rec["board_timestamp"] = None
    rec["price_timestamp"] = None
    rec["ingress_timestamp"] = None
    rec["board_age_t0_sec"] = None
    rec["price_age_t0_sec"] = None
    rec["ingress_age_t0_sec"] = None
    if not snap.get("ok"):
        return rec
    i = int(snap["i"])
    t_event = float(board["t"][i]) if _finite(board["t"][i]) else None
    t_board_raw = float(extra["board_raw"][i]) if _finite(extra["board_raw"][i]) else None
    t_price = float(extra["price_epoch"][i]) if _finite(extra["price_epoch"][i]) else None
    t_ing = float(extra["ingress"][i]) if _finite(extra["ingress"][i]) else None
    rec["board_timestamp_epoch"] = t_board_raw
    rec["price_timestamp_epoch"] = t_price
    rec["ingress_timestamp_epoch"] = t_ing
    rec["board_timestamp"] = _iso(t_board_raw)
    rec["price_timestamp"] = _iso(t_price)
    rec["ingress_timestamp"] = _iso(t_ing)
    rec["event_t_snap"] = _iso(t_event)
    if t_board_raw is not None and t0 is not None and float(t_board_raw) > float(t0) + 1e-12:
        rec["board_raw_after_t0"] = True
        rec["board_age_t0_sec"] = None
    else:
        rec["board_raw_after_t0"] = False
        rec["board_age_t0_sec"] = _age_t0(t0, t_board_raw)
    used = rec.get("BOARD_FRESHNESS_CLOCK_EPOCH")
    if _finite(used) and float(used) > float(t0) + 1e-12:
        leak["FUTURE_QUOTE_CARRYBACK_N"] = int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0) + 1
    rec["price_age_t0_sec"] = _age_t0(t0, t_price)
    rec["ingress_age_t0_sec"] = _age_t0(t0, t_ing)
    return rec


def replay_reconcile_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "VIRTUAL_FILL_N": 0,
        "PNL_EVAL_N": 0,
        "FIXED180_PNL_N": 0,
        "FUTURE_BOARD_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_BOARD_N": 0,
        "QUEUE_ASSUMPTION_N": 0,
        "QUEUE_FILL_N": 0,
        "OPTIMISTIC_TOUCH_N": 0,
        "OPTIMISTIC_FILL_N": 0,
        "TOUCH_FILL_N": 0,
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N": 0,
        "BOARD_CLOCK_UNRESOLVED_N": 0,
        "BOARD_EVENT_AFTER_EVENT_T_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "E4_CHANGE_N": 0,
        "FRESHNESS_CHANGE_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "EXIT_SIM_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "FALLBACK_MARKET_N": 0,
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
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FILL_REPLAY_ON_FUTURE_QUOTE_N": 0,
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
            "signal_n": 0,
        }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in signals if _bare(r.get("symbol"))}
        bufs: dict[str, _Buf] = {s: _Buf() for s in needed}
        srcs: dict[str, list[str]] = {s: [] for s in needed}
        clocks: dict[str, list[float]] = {s: [] for s in needed}
        price_ages: dict[str, list[float]] = {s: [] for s in needed}
        ingress: dict[str, list[float]] = {s: [] for s in needed}
        board_raw: dict[str, list[float]] = {s: [] for s in needed}
        price_ep: dict[str, list[float]] = {s: [] for s in needed}
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
            t_ing = _ingress_epoch(rec, pay)
            t_brd = _board_quote_t(pay)
            t_px = _parse_iso(pay.get("CurrentPriceTime"))
            ingress[sym].append(float(t_ing) if t_ing is not None else NAN)
            board_raw[sym].append(float(t_brd) if t_brd is not None else NAN)
            price_ep[sym].append(float(t_px) if t_px is not None else NAN)
            last_et = float(et)
            events_n += 1
            if events_n % 200000 == 0:
                print(f"{day} v25 reconcile stream kept={events_n} last_et={last_et}", flush=True)
        views = {s: bufs[s].view() for s in needed}
        metas = {
            s: {
                "source": np.asarray(srcs[s], dtype=object),
                "clock": np.asarray(clocks[s], dtype=float),
                "price_fresh": np.asarray(price_ages[s], dtype=float),
            }
            for s in needed
        }
        extras = {
            s: {
                "ingress": np.asarray(ingress[s], dtype=float),
                "board_raw": np.asarray(board_raw[s], dtype=float),
                "price_epoch": np.asarray(price_ep[s], dtype=float),
            }
            for s in needed
        }
        rows = []
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rec = corrected_row(sig, views[s], metas[s], am_end=am_end, leak=leak)
            rec = attach_t0_clocks(rec, views[s], extras[s], leak=leak)
            rec["PNL_EVAL"] = False
            rec["VIRTUAL_FILL"] = False
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(f"{day} v25 reconcile kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
        del bufs, views, metas, extras, srcs, clocks, price_ages, ingress, board_raw, price_ep
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


def save_v25_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v25_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}

"""Post-trigger BB reclaim / adverse extension / break-even first events. No new EXIT. No 20260903."""
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
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators
from research.simple_tech_redesign.branch_u_bb_harvest import first_bb_lower_break
from research.simple_tech_redesign.branch_u_bb_spec import EXIT_REASON
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS
from research.simple_tech_redesign.exit_lifecycle_harvest import _arr_at
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok
from research.simple_tech_redesign.branch_u_false_break_rca_spec import EVENT_RANK, SEQUENCE_LABELS, spec_sha256_false_break
from small_paper.v1r_live_dual_lane import session_end_for_position

SEQ_CACHE = RESEARCH_CACHE / "branch_u_false_break_sequence_rca"
SPEC_SHA = spec_sha256_false_break()


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


def _u_triggered(row: dict[str, Any]) -> bool:
    bu = dict(row.get("branch_u") or {})
    tx = dict(row.get("treatment_exit") or {})
    return bool(
        bu.get("branch_u_exit_triggered")
        or bu.get("u_bb_lower_break")
        or str(tx.get("reason") or "") == EXIT_REASON
    )


def _trigger_t(row: dict[str, Any]) -> Optional[float]:
    bu = dict(row.get("branch_u") or {})
    tx = dict(row.get("treatment_exit") or {})
    return _f(bu.get("trigger_time") or bu.get("u_bb_lower_break_time")) or _f(tx.get("trigger_event_time"))


def pred_bb_reclaim(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
    cl, bb = _arr_at(ind, "close", i), _arr_at(ind, "bb_lower", i)
    return None if cl is None or bb is None else bool(cl >= bb)


def _bar_index_at_finalize(tf1: dict[str, np.ndarray], event_t: float) -> Optional[int]:
    fin = tf1.get("finalize_t")
    if fin is None or int(fin.size) == 0:
        return None
    i = int(np.searchsorted(fin, float(event_t), side="left"))
    if i < int(fin.size) and _finite(fin[i]) and abs(float(fin[i]) - float(event_t)) <= 1e-12:
        return i
    return None


def _trigger_bar_index(tf1: dict[str, np.ndarray], event_t: float, bar_t: Any) -> Optional[int]:
    i = _bar_index_at_finalize(tf1, float(event_t))
    if i is not None:
        return i
    me = tf1.get("minute_epoch")
    bt = _f(bar_t)
    if bt is not None and me is not None:
        for k in range(int(me.size)):
            if _finite(me[k]) and abs(float(me[k]) - float(bt)) <= 1e-12:
                return int(k)
    fin = tf1.get("finalize_t")
    if fin is None or int(fin.size) == 0:
        return None
    j = int(np.searchsorted(fin, float(event_t), side="left"))
    cand: list[tuple[float, int]] = []
    if 0 <= j < int(fin.size) and _finite(fin[j]):
        cand.append((abs(float(fin[j]) - float(event_t)), int(j)))
    if j > 0 and _finite(fin[j - 1]):
        cand.append((abs(float(fin[j - 1]) - float(event_t)), int(j - 1)))
    if not cand:
        return None
    cand.sort()
    if cand[0][0] <= 1e-6:
        return int(cand[0][1])
    return None


def first_reclaim(tf1: dict[str, np.ndarray], *, start_t: float, end_t: float) -> dict[str, Any]:
    out = {"hit": False, "event_t": None, "bar_t": None, "close": None, "bb_lower": None, "bar_i": None}
    fin = tf1.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    if n <= 0:
        return out
    j = int(np.searchsorted(fin, float(start_t), side="right"))
    for i in range(j, n):
        ft = float(fin[i]) if _finite(fin[i]) else None
        if ft is None:
            continue
        if ft > float(end_t) + 1e-12:
            break
        if pred_bb_reclaim(tf1, int(i)) is True:
            out.update(
                {
                    "hit": True,
                    "event_t": ft,
                    "bar_t": _arr_at(tf1, "minute_epoch", int(i)),
                    "close": _arr_at(tf1, "close", int(i)),
                    "bb_lower": _arr_at(tf1, "bb_lower", int(i)),
                    "bar_i": int(i),
                }
            )
            return out
    return out


def first_adverse_extension(
    tf1: dict[str, np.ndarray], *, start_t: float, end_t: float, trigger_low: float
) -> dict[str, Any]:
    out = {"hit": False, "event_t": None, "bar_t": None, "low": None, "trigger_low": float(trigger_low), "bar_i": None}
    fin = tf1.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    if n <= 0:
        return out
    j = int(np.searchsorted(fin, float(start_t), side="right"))
    for i in range(j, n):
        ft = float(fin[i]) if _finite(fin[i]) else None
        if ft is None:
            continue
        if ft > float(end_t) + 1e-12:
            break
        lo = _arr_at(tf1, "low", int(i))
        if lo is None:
            continue
        if float(lo) < float(trigger_low) - 1e-12:
            out.update(
                {
                    "hit": True,
                    "event_t": ft,
                    "bar_t": _arr_at(tf1, "minute_epoch", int(i)),
                    "low": float(lo),
                    "bar_i": int(i),
                }
            )
            return out
    return out


def first_break_even_after(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    start_t: float,
    fill_px: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out = {"hit": False, "event_t": None, "be_bid": None, "be_yen": None}
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return out
    i0 = int(np.searchsorted(t, float(start_t), side="right"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 <= float(start_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _clock_ok(meta, i, ti, leak) or not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        yen = float(compute_pnl_yen_100(float(fill_px), bid, side="long"))
        if yen >= 0.0:
            return {"hit": True, "event_t": ti, "be_bid": bid, "be_yen": yen}
    return out


def classify_first(events: list[dict[str, Any]]) -> dict[str, Any]:
    hits = [e for e in events if e.get("hit") and _f(e.get("event_t")) is not None]
    if not hits:
        return {
            "first_event": None,
            "first_label": "NO_RESOLUTION_BEFORE_SESSION_CLOSE",
            "first_t": None,
            "tie": False,
        }
    ordered = sorted(
        hits,
        key=lambda e: (
            float(e["event_t"]),
            int(EVENT_RANK.get(str(e.get("name") or ""), 9)),
            str(e.get("name") or ""),
        ),
    )
    top = ordered[0]
    tie = False
    if len(ordered) > 1 and abs(float(ordered[1]["event_t"]) - float(top["event_t"])) <= 1e-12:
        tie = True
    name = str(top.get("name") or "")
    label = {
        "BB_RECLAIM": "RECLAIM_FIRST",
        "ADVERSE_EXTENSION": "ADVERSE_EXTENSION_FIRST",
        "BREAK_EVEN": "BREAK_EVEN_FIRST",
    }.get(name, "NO_RESOLUTION_BEFORE_SESSION_CLOSE")
    return {"first_event": name, "first_label": label, "first_t": top.get("event_t"), "tie": bool(tie), "winner": top}


def scan_sequence(
    row: dict[str, Any],
    tf1: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    trig = _trigger_t(row)
    fill_px = _f(row.get("fill_price"))
    bu = dict(row.get("branch_u") or {})
    cx = dict(row.get("control_exit") or {})
    tx = dict(row.get("treatment_exit") or {})
    out: dict[str, Any] = {
        "date": row.get("date"),
        "symbol": _bare(row.get("symbol")),
        "t0": row.get("t0"),
        "fill_t": row.get("fill_t"),
        "fill_price": fill_px,
        "fill_role": row.get("fill_role"),
        "path_type": row.get("path_type"),
        "trigger_t": trig,
        "trigger_bar_t": bu.get("trigger_bar_time") or bu.get("u_bb_lower_break_bar_time"),
        "close_at_trigger": bu.get("close_at_trigger"),
        "bb_lower_at_trigger": bu.get("bb_lower_at_trigger"),
        "immediate_reason": tx.get("reason"),
        "immediate_u_pnl": tx.get("pnl_yen_100"),
        "session_close_pnl": cx.get("pnl_yen_100"),
        "immediate_exit_t": tx.get("exit_t"),
        "session_close_exit_t": cx.get("exit_t"),
        "actual_immediate_u_exit": str(tx.get("reason") or "") == EXIT_REASON,
        "lifecycle_state": bu.get("lifecycle_state"),
        "never_break_even": (not bool(bu.get("break_even_reached"))) if bu else None,
        "cache_be_t": bu.get("first_break_even_time"),
        "trigger_low": None,
        "reclaim": None,
        "adverse": None,
        "break_even": None,
        "first_label": "NO_RESOLUTION_BEFORE_SESSION_CLOSE",
        "bb_recompute_match": None,
    }
    if trig is None or fill_px is None or tf1.get("finalize_t") is None:
        leak["SEQUENCE_SKIP_N"] = int(leak.get("SEQUENCE_SKIP_N") or 0) + 1
        return out
    be_end = _f(bu.get("first_break_even_time"))
    fill_t = _f(row.get("fill_t")) or float(trig)
    recomputed = first_bb_lower_break(tf1, start_t=float(fill_t), end_t=be_end)
    if recomputed.get("hit") and _f(recomputed.get("event_t")) is not None:
        out["bb_recompute_match"] = abs(float(recomputed["event_t"]) - float(trig)) <= 1e-12
        if not out["bb_recompute_match"]:
            leak["BB_TRIGGER_DRIFT_N"] = int(leak.get("BB_TRIGGER_DRIFT_N") or 0) + 1
    else:
        out["bb_recompute_match"] = False
        leak["BB_TRIGGER_DRIFT_N"] = int(leak.get("BB_TRIGGER_DRIFT_N") or 0) + 1
    i_trig = _trigger_bar_index(tf1, float(trig), out.get("trigger_bar_t"))
    trigger_low = _arr_at(tf1, "low", int(i_trig)) if i_trig is not None else None
    out["trigger_low"] = trigger_low
    out["trigger_bar_i"] = i_trig
    rec = first_reclaim(tf1, start_t=float(trig), end_t=float(am_end))
    rec["name"] = "BB_RECLAIM"
    adv = {"hit": False, "event_t": None, "name": "ADVERSE_EXTENSION", "trigger_low": trigger_low}
    if trigger_low is not None:
        adv = first_adverse_extension(tf1, start_t=float(trig), end_t=float(am_end), trigger_low=float(trigger_low))
        adv["name"] = "ADVERSE_EXTENSION"
    else:
        leak["TRIGGER_LOW_MISS_N"] = int(leak.get("TRIGGER_LOW_MISS_N") or 0) + 1
    be = first_break_even_after(board, meta, start_t=float(trig), fill_px=float(fill_px), sess_end=float(am_end), leak=leak)
    be["name"] = "BREAK_EVEN"
    picked = classify_first([rec, adv, be])
    out["reclaim"] = rec
    out["adverse"] = adv
    out["break_even"] = be
    out["first_event"] = picked.get("first_event")
    out["first_label"] = picked.get("first_label")
    out["first_t"] = picked.get("first_t")
    out["tie_at_first"] = picked.get("tie")
    cache_be = _f(bu.get("first_break_even_time"))
    rec_be = _f(be.get("event_t"))
    if cache_be is None and rec_be is None:
        out["be_cache_match"] = True
    elif cache_be is not None and rec_be is not None:
        out["be_cache_match"] = abs(float(cache_be) - float(rec_be)) <= 1e-12
        if not out["be_cache_match"]:
            leak["BE_CACHE_DRIFT_N"] = int(leak.get("BE_CACHE_DRIFT_N") or 0) + 1
    elif cache_be is not None and float(cache_be) + 1e-12 <= float(trig):
        out["be_cache_match"] = True
    else:
        out["be_cache_match"] = False
        leak["BE_CACHE_DRIFT_N"] = int(leak.get("BE_CACHE_DRIFT_N") or 0) + 1
    iu = _f(tx.get("pnl_yen_100"))
    sc = _f(cx.get("pnl_yen_100"))
    out["immediate_minus_close"] = None if iu is None or sc is None else float(iu) - float(sc)
    if out["first_label"] not in SEQUENCE_LABELS:
        out["first_label"] = "NO_RESOLUTION_BEFORE_SESSION_CLOSE"
    return out


def stream_day_views(
    day: str,
    capture: Path,
    symbols: set[str],
    leak: dict[str, Any],
) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
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
        builders[sym].on_event(
            et=float(et),
            px=row["px"] if row["px"] == row["px"] else None,
            cum_vol=row.get("cum_vol"),
            bid=row["bid"] if row["bid"] == row["bid"] else None,
            ask=row["ask"] if row["ask"] == row["ask"] else None,
            continuous=bool(row.get("continuous")),
        )
        events_n += 1
        if events_n % 200000 == 0:
            print(f"{day} false-break stream kept={events_n}", flush=True)
    tf1_by = {}
    views = {}
    metas = {}
    integ_fail = 0
    for s in symbols:
        builders[s].close_session()
        raw = builders[s].as_arrays()
        integ1 = bar_integrity(raw, am_start=am_start, am_end=am_end)
        if not integ1.get("ok"):
            integ_fail += 1
        tf1_by[s] = attach_indicators(raw) if int(raw["minute_epoch"].size) > 0 else {}
        views[s] = bufs[s].view()
        metas[s] = {
            "source": np.asarray(srcs[s], dtype=object),
            "clock": np.asarray(clocks[s], dtype=float),
            "price_fresh": np.asarray(price_ages[s], dtype=float),
        }
    return {
        "tf1_by": tf1_by,
        "views": views,
        "metas": metas,
        "am_end": am_end,
        "events_n": events_n,
        "integ_fail_n": integ_fail,
    }


def harvest_day(day: str, *, cohort: str, spec_sha: str, today: str = TODAY) -> dict[str, Any]:
    if day == str(today) or day in FORBIDDEN_DAYS:
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = SEQ_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    ubody = load_u_day(day, cohort=cohort)
    if not ubody.get("ok"):
        return {"ok": False, "blocker": f"u_cache_missing:{day}", "date": day}
    triggered = [r for r in list(ubody.get("rows") or []) if r.get("actual_filled") and _u_triggered(r)]
    leak = {
        "SEQUENCE_SKIP_N": 0,
        "BB_TRIGGER_DRIFT_N": 0,
        "TRIGGER_LOW_MISS_N": 0,
        "BE_CACHE_DRIFT_N": 0,
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "BOARD_CLOCK_UNRESOLVED_N": 0,
    }
    if not triggered:
        body = {
            "ok": True,
            "date": day,
            "cohort": cohort,
            "spec_sha": spec_sha,
            "u_spec_sha": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "triggered_n": 0,
            "rows": [],
            "leak": leak,
            "events_n": 0,
        }
        SEQ_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    if str(capture).replace("\\", "/").find(str(today)) >= 0 and day == str(today):
        return {"ok": False, "blocker": "ACTIVE_CAPTURE", "date": day}
    symbols = {_bare(r.get("symbol")) for r in triggered if _bare(r.get("symbol"))}
    print(f"{cohort} {day} false-break stream symbols={len(symbols)} triggered={len(triggered)}", flush=True)
    packed = stream_day_views(day, capture, symbols, leak)
    rows = []
    for r in triggered:
        s = _bare(r.get("symbol"))
        rec = dict(r)
        rec["sequence"] = scan_sequence(
            rec,
            packed["tf1_by"].get(s) or {},
            packed["views"].get(s) or {},
            packed["metas"].get(s) or {},
            am_end=float(packed["am_end"]),
            leak=leak,
        )
        rows.append(rec["sequence"])
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "u_spec_sha": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
        "triggered_n": len(triggered),
        "rows": rows,
        "leak": leak,
        "events_n": packed.get("events_n"),
        "integ_fail_n": packed.get("integ_fail_n"),
    }
    SEQ_CACHE.mkdir(parents=True, exist_ok=True)
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
    for day in days:
        body = harvest_day(day, cohort=cohort, spec_sha=spec_sha, today=today)
        if not body.get("ok"):
            return body
        bodies.append(body)
        rows.extend(list(body.get("rows") or []))
        for k, v in dict(body.get("leak") or {}).items():
            leak_sum[k] = int(leak_sum.get(k) or 0) + int(v or 0)
    return {"ok": True, "cohort": cohort, "days": list(days), "rows": rows, "day_bodies": bodies, "leak": leak_sum}

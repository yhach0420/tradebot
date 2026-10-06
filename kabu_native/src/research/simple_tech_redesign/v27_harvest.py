"""V27 sealed replay: V26 fills + completed-bar deterioration episodes. Exact V26 primitives. No EXIT policy."""
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
from research.simple_tech_entry_family.stages import attach_indicators
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v26_harvest import joint_row, primitive_true
from research.simple_tech_redesign.v26_spec import PRIMITIVES, TF_TO_INTERNAL, TFS
from research.simple_tech_redesign.v27_spec import PROP_CLASSES
from small_paper.v1r_live_dual_lane import session_end_for_position

V27_CACHE = RESEARCH_CACHE / "v27_exit_state_sequence_rca"


def primitive_known(pid: str, ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
    """V26 primitive_true when inputs are finite; None if the bar cannot evaluate."""
    if i < 0:
        return None
    n = int(ind.get("close").size) if ind.get("close") is not None else 0
    if i >= n:
        return None

    def ok(key: str, j: int) -> bool:
        arr = ind.get(key)
        if arr is None or j < 0 or j >= int(arr.size):
            return False
        v = float(arr[j])
        return v == v

    if pid == "A_EMA_STRUCTURE_LOSS":
        if not (ok("ema9", i) and ok("ema21", i)):
            return None
    elif pid == "B_EMA_SHORT_SLOPE_LOSS":
        if i < 1:
            return False
        if not (ok("ema9", i) and ok("ema9", i - 1)):
            return None
    elif pid == "C_PRICE_STRUCTURE_LOSS":
        if not (ok("close", i) and ok("ema9", i)):
            return None
    elif pid == "D_BB_STRUCTURE_LOSS":
        if not (ok("close", i) and ok("bb_mid", i)):
            return None
    elif pid == "E_RCI_ROLLOVER":
        if i < 1:
            return False
        if not (ok("rci9", i) and ok("rci9", i - 1)):
            return None
    elif pid == "F_VOLUME_DETERIORATION":
        if i < 1:
            return False
        if not (ok("close", i) and ok("ema9", i) and ok("volume", i) and ok("volume", i - 1)):
            return None
    else:
        return None
    return bool(primitive_true(pid, ind, i))


def _episodes_for_pid(ind: dict[str, np.ndarray], pid: str, first_i: int, n: int) -> list[dict[str, Any]]:
    fin = ind["finalize_t"]
    episodes: list[dict[str, Any]] = []
    in_ep = False
    start_off = start_i = None
    prev: Optional[bool] = False
    last_off = last_i = None
    for off, i in enumerate(range(int(first_i), n), start=1):
        known = primitive_known(pid, ind, int(i))
        if known is None:
            continue
        if (not in_ep) and (prev is False) and known is True:
            in_ep = True
            start_off = int(off)
            start_i = int(i)
        if in_ep and known is False:
            episodes.append(
                {
                    "start_bar": int(start_off),
                    "end_bar": int(off - 1) if last_off is not None else int(start_off),
                    "duration_completed_bars": int(off - int(start_off)),
                    "start_t": float(fin[start_i]) if start_i is not None else None,
                    "end_t": float(fin[last_i]) if last_i is not None else float(fin[start_i]),
                    "recovered_before_session_end": True,
                }
            )
            in_ep = False
            start_off = start_i = None
        if known is True:
            last_off = int(off)
            last_i = int(i)
        prev = known
    if in_ep and start_off is not None:
        episodes.append(
            {
                "start_bar": int(start_off),
                "end_bar": int(last_off if last_off is not None else start_off),
                "duration_completed_bars": int((last_off if last_off is not None else start_off) - int(start_off) + 1),
                "start_t": float(fin[start_i]) if start_i is not None else None,
                "end_t": float(fin[last_i]) if last_i is not None else None,
                "recovered_before_session_end": False,
            }
        )
    return episodes


def _prop_class(starts: dict[str, Optional[float]]) -> Optional[str]:
    t1 = starts.get("1m")
    if not _finite(t1):
        return None
    t3 = starts.get("3m")
    t5 = starts.get("5m")
    has3 = _finite(t3) and float(t3) + 1e-12 >= float(t1)
    has5 = _finite(t5) and float(t5) + 1e-12 >= float(t1)
    if has3 and has5:
        return "P4_1M_TO_3M_TO_5M"
    if has3:
        return "P2_1M_TO_3M"
    if has5:
        return "P3_1M_TO_5M"
    return "P1_1M_ONLY"


def eval_state_sequence(by_tf: dict[str, dict[str, np.ndarray]], *, fill_t: float) -> dict[str, Any]:
    tf_pack: dict[str, Any] = {}
    first_starts: dict[str, dict[str, Optional[float]]] = {pid: {} for pid in PRIMITIVES}
    for tf in TFS:
        ind = by_tf.get(TF_TO_INTERNAL[tf]) or {}
        fin = ind.get("finalize_t")
        n = int(fin.size) if fin is not None else 0
        first_i = None
        if n:
            j = int(np.searchsorted(fin, float(fill_t), side="right"))
            if j < n:
                first_i = j
        post_n = (n - int(first_i)) if first_i is not None else 0
        pid_map: dict[str, Any] = {"post_fill_bar_n": int(post_n), "observable": bool(first_i is not None and post_n > 0)}
        for pid in PRIMITIVES:
            eps = _episodes_for_pid(ind, pid, int(first_i), n) if first_i is not None else []
            first = eps[0] if eps else None
            pid_map[pid] = {
                "episode_n": len(eps),
                "episodes": eps,
                "first_duration": None if first is None else int(first["duration_completed_bars"]),
                "first_recovered": None if first is None else bool(first["recovered_before_session_end"]),
                "first_start_t": None if first is None else first.get("start_t"),
            }
            first_starts[pid][tf] = first.get("start_t") if first else None
        tf_pack[tf] = pid_map
    prop: dict[str, Any] = {}
    for pid in PRIMITIVES:
        starts = first_starts[pid]
        timed = [(tf, float(starts[tf])) for tf in TFS if _finite(starts.get(tf))]
        timed.sort(key=lambda x: (x[1], TFS.index(x[0])))
        seq = ">".join(tf for tf, _ in timed) if timed else None
        first_tf = timed[0][0] if timed else None
        pclass = _prop_class(starts)
        if pclass is not None and pclass not in PROP_CLASSES:
            pclass = None
        prop[pid] = {
            "first_tf": first_tf,
            "propagation_sequence": seq,
            "prop_class": pclass,
            "t_1m": starts.get("1m"),
            "t_3m": starts.get("3m"),
            "t_5m": starts.get("5m"),
        }
    return {"by_tf": tf_pack, "propagation": prop}


def replay_sequence_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "FUTURE_BAR_N": 0,
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
        "BAR_COUNT_SEARCH_N": 0,
        "COMBINATION_SEARCH_N": 0,
        "WAIT_EXTENSION_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "SECOND_FALLBACK_N": 0,
        "FALLBACK_MARKET_N": 0,
        "TIME_STOP_N": 0,
        "EXIT_POLICY_N": 0,
        "EXIT_COMBINATION_N": 0,
        "MIXED_TF_RULE_N": 0,
        "RCI_LEVEL_THRESHOLD_N": 0,
        "VOLUME_MULT_SEARCH_N": 0,
        "NOT_ELIGIBLE_N": 0,
        "INSIDE_COLLAPSE_N": 0,
        "ASK_CROSS_LIMIT_N": 0,
        "ASK_CROSS_FILL_N": 0,
        "INSIDE_ASK_CROSS_FILL_N": 0,
        "ASK_FALLBACK_ELIGIBLE_N": 0,
        "ASK_FALLBACK_FILL_N": 0,
        "NO_ASK0_N": 0,
        "MID0_MISS_N": 0,
        "FULL_MISS_N": 0,
        "GROSS_MISS_N": 0,
        "DUAL_BID_NE_EXEC_BID_N": 0,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FILL_REPLAY_ON_FUTURE_QUOTE_N": 0,
        "PARTIAL_BUCKET_N": 0,
        "GAP_BUCKET_N": 0,
        "LATE_FINALIZE_N": 0,
        "SESSION_TAIL_SKIP_N": 0,
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
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in needed}
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
            builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            last_et = float(et)
            events_n += 1
            if events_n % 200000 == 0:
                print(f"{day} v27 sequence stream kept={events_n} last_et={last_et}", flush=True)
        tf_by_sym: dict[str, dict[str, dict[str, np.ndarray]]] = {}
        integ_fail = 0
        for s in needed:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ1 = bar_integrity(raw, am_start=am_start, am_end=am_end)
            leak["FUTURE_BAR_N"] = int(leak.get("FUTURE_BAR_N") or 0) + int(integ1.get("FUTURE_BAR_N") or 0)
            if not integ1.get("ok"):
                integ_fail += 1
            by_tf: dict[str, dict[str, np.ndarray]] = {}
            if int(raw["minute_epoch"].size) > 0:
                by_tf["tf1"] = attach_indicators(raw)
            for tf, width in (("tf3", 180.0), ("tf5", 300.0)):
                arr, alk = aggregate_bars(raw, width_sec=float(width), am_start=am_start, am_end=am_end)
                for k in ("PARTIAL_BUCKET_N", "GAP_BUCKET_N", "LATE_FINALIZE_N", "SESSION_TAIL_SKIP_N"):
                    leak[k] = int(leak.get(k) or 0) + int(alk.get(k) or 0)
                integ = agg_integrity(arr, width_sec=float(width), am_start=am_start, am_end=am_end)
                leak["FUTURE_BAR_N"] = int(leak.get("FUTURE_BAR_N") or 0) + int(integ.get("FUTURE_BAR_N") or 0)
                if not integ.get("ok"):
                    integ_fail += 1
                if int(arr["minute_epoch"].size) > 0:
                    by_tf[tf] = attach_indicators(arr)
            tf_by_sym[s] = by_tf
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
            rec = joint_row(sig, views[s], metas[s], tf_by_sym.get(s) or {}, am_end=am_end, leak=leak)
            rec["state_sequence"] = None
            rec["EXIT_POLICY_CREATED"] = False
            if rec.get("actual_filled") and _finite(rec.get("fill_t")):
                rec["state_sequence"] = eval_state_sequence(tf_by_sym.get(s) or {}, fill_t=float(rec["fill_t"]))
            rec.pop("exit_eval", None)
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(
            f"{day} v27 sequence kept_events={events_n} signals={len(rows)} "
            f"integ_fail={integ_fail} last_et={last_et}",
            flush=True,
        )
        del bufs, views, metas, builders, tf_by_sym, srcs, clocks, price_ages
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "v12_spec_sha": V12_SPEC_SHA256_EXPECTED,
            "events_n": events_n,
            "last_et": last_et,
            "integ_fail_n": integ_fail,
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


def save_v27_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "v12_spec_sha": body.get("v12_spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "integ_fail_n": body.get("integ_fail_n"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
            "signal_n": body.get("signal_n"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v27_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}

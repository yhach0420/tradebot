"""Sealed Branch U harvest: UNPROVEN U_BB_LOWER_BREAK then first causal Bid1. Control = session-close."""
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
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_exit_family.v14_harvest import _snap
from research.simple_tech_redesign.branch_u_bb_spec import EXIT_REASON, SHARES, TRIGGER_NAME
from research.simple_tech_redesign.exit_lifecycle_harvest import _arr_at, _first_bar_flag, walk_break_even
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v26_harvest import joint_row
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok
from research.simple_tech_redesign.v28_spec import SHARES as V28_SHARES
from small_paper.v1r_live_dual_lane import session_end_for_position

BRANCH_U_CACHE = RESEARCH_CACHE / "branch_u_bb_lower_exit_one_shot"


def pred_bb_lower(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
    cl, bb = _arr_at(ind, "close", i), _arr_at(ind, "bb_lower", i)
    return None if cl is None or bb is None else bool(cl < bb)


def first_bb_lower_break(
    tf1: dict[str, np.ndarray], *, start_t: float, end_t: Optional[float]
) -> dict[str, Any]:
    out = {"hit": False, "event_t": None, "bar_t": None, "close": None, "bb_lower": None}
    fin = tf1.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    if n <= 0:
        return out
    j = int(np.searchsorted(fin, float(start_t), side="right"))
    for i in range(j, n):
        ft = float(fin[i]) if _finite(fin[i]) else None
        if ft is None:
            continue
        if end_t is not None and ft + 1e-12 >= float(end_t):
            break
        if pred_bb_lower(tf1, int(i)) is True:
            out["hit"] = True
            out["event_t"] = ft
            out["bar_t"] = _arr_at(tf1, "minute_epoch", int(i))
            out["close"] = _arr_at(tf1, "close", int(i))
            out["bb_lower"] = _arr_at(tf1, "bb_lower", int(i))
            return out
    return out


def _meta_at(meta: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    out: dict[str, Any] = {"clock_epoch": None, "board_source": None, "price_fresh_sec": None}
    clk = meta.get("clock") if meta else None
    src = meta.get("source") if meta else None
    pf = meta.get("price_fresh") if meta else None
    if clk is not None and int(getattr(clk, "size", 0) or 0) > i and _finite(clk[i]):
        out["clock_epoch"] = float(clk[i])
    if src is not None and int(getattr(src, "size", 0) or 0) > i:
        out["board_source"] = str(src[i])
    if pf is not None and int(getattr(pf, "size", 0) or 0) > i and _finite(pf[i]):
        out["price_fresh_sec"] = float(pf[i])
    return out


def _freshness(board: dict[str, np.ndarray], meta: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    snap = _snap(board, i)
    ev = _meta_at(meta, i)
    return {
        "fresh_sec": snap.get("fresh_sec"),
        "bid_qty": snap.get("bid_qty"),
        "executable": snap.get("executable"),
        "special": snap.get("special"),
        "clock_epoch": ev.get("clock_epoch"),
        "board_source": ev.get("board_source"),
        "price_fresh_sec": ev.get("price_fresh_sec"),
        "freshness_ok": bool(_bid_ok(board, i)),
    }


def first_causal_bid_evidence(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    event_t: float,
    fill_px: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out: dict[str, Any] = {
        "exit_t": None,
        "exit_bid": None,
        "miss": True,
        "pre_decision": False,
        "freshness_evidence": None,
        "board_i": None,
    }
    if t is None or int(t.size) == 0 or not _finite(fill_px):
        return out
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(event_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _clock_ok(meta, i, ti, leak):
            continue
        if not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        if ti + 1e-12 < float(event_t):
            out["pre_decision"] = True
            leak["PRE_DECISION_EXIT_N"] = int(leak.get("PRE_DECISION_EXIT_N") or 0) + 1
            leak["PRE_BAR_EXIT_N"] = int(leak.get("PRE_BAR_EXIT_N") or 0) + 1
            continue
        return {
            "exit_t": ti,
            "exit_bid": bid,
            "miss": False,
            "pre_decision": False,
            "freshness_evidence": _freshness(board, meta, i),
            "board_i": int(i),
        }
    return out


def last_session_bid_evidence(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out: dict[str, Any] = {"exit_t": None, "exit_bid": None, "miss": True, "freshness_evidence": None, "board_i": None}
    if t is None or int(t.size) == 0:
        return out
    for i in range(int(t.size) - 1, -1, -1):
        ti = float(t[i])
        if ti > float(sess_end) + 1e-12:
            continue
        if ti + 1e-12 < float(fill_t):
            break
        if not _clock_ok(meta, i, ti, leak):
            continue
        if not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        return {
            "exit_t": ti,
            "exit_bid": bid,
            "miss": False,
            "freshness_evidence": _freshness(board, meta, i),
            "board_i": int(i),
        }
    leak["SESSION_CLOSE_MISS_N"] = int(leak.get("SESSION_CLOSE_MISS_N") or 0) + 1
    return out


def _pack_exit(*, reason: str, fill_px: float, pack: dict[str, Any], leak: dict[str, Any], extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    miss = bool(pack.get("miss"))
    exit_t = pack.get("exit_t")
    exit_bid = pack.get("exit_bid")
    yen = None
    if (not miss) and _finite(exit_bid) and _finite(fill_px):
        yen = float(compute_pnl_yen_100(float(fill_px), float(exit_bid), side="long"))
    if miss:
        leak["EXIT_MISS_N"] = int(leak.get("EXIT_MISS_N") or 0) + 1
    if reason == EXIT_REASON:
        leak["BRANCH_U_EXIT_N"] = int(leak.get("BRANCH_U_EXIT_N") or 0) + 1
    elif reason == "SESSION_CLOSE":
        leak["SESSION_CLOSE_EXIT_N"] = int(leak.get("SESSION_CLOSE_EXIT_N") or 0) + 1
    body = {
        "reason": reason,
        "exit_t": float(exit_t) if _finite(exit_t) else None,
        "exit_bid": float(exit_bid) if _finite(exit_bid) else None,
        "pnl_yen_100": yen,
        "miss": bool(miss),
        "shares": int(SHARES),
        "freshness_evidence": pack.get("freshness_evidence"),
    }
    if extra:
        body.update(extra)
    return body


def attach_u_exits(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    tf1: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec["branch_u"] = None
    rec["control_exit"] = None
    rec["treatment_exit"] = None
    if not rec.get("actual_filled") or not _finite(rec.get("fill_t")) or not _finite(rec.get("fill_price")):
        return rec
    fill_t = float(rec["fill_t"])
    fill_px = float(rec["fill_price"])
    be = walk_break_even(
        board, meta, fill_t=fill_t, fill_px=fill_px, sess_end=float(am_end), pullback_low=None, leak=leak
    )
    u_end = float(be["be_t"]) if be.get("break_even_reached") else None
    hit_flag, ht_flag = _first_bar_flag(tf1, start_t=fill_t, end_t=u_end, pred=pred_bb_lower)
    bb = first_bb_lower_break(tf1, start_t=fill_t, end_t=u_end)
    if bool(hit_flag) != bool(bb.get("hit")):
        leak["BB_DEF_DRIFT_N"] = int(leak.get("BB_DEF_DRIFT_N") or 0) + 1
    if hit_flag and _finite(ht_flag) and _finite(bb.get("event_t")) and abs(float(ht_flag) - float(bb["event_t"])) > 1e-12:
        leak["BB_DEF_DRIFT_N"] = int(leak.get("BB_DEF_DRIFT_N") or 0) + 1
    sess = last_session_bid_evidence(board, meta, fill_t=fill_t, sess_end=float(am_end), leak=leak)
    control_seq = [
        {"seq": 1, "event": "FILL", "t": fill_t, "price": fill_px},
        {
            "seq": 2,
            "event": "SESSION_CLOSE",
            "t": sess.get("exit_t"),
            "bid": sess.get("exit_bid"),
            "miss": bool(sess.get("miss")),
        },
    ]
    control = _pack_exit(
        reason="SESSION_CLOSE",
        fill_px=fill_px,
        pack=sess,
        leak=leak,
        extra={"exit_event_sequence": control_seq, "trigger_event_time": None, "trigger_bar_time": None},
    )
    rec["control_exit"] = control
    bb_before_be = bool(bb.get("hit"))
    be_reached = bool(be.get("break_even_reached"))
    if be_reached and (not bb_before_be):
        lifecycle_state = "PROVEN"
    else:
        lifecycle_state = "UNPROVEN"
    u: dict[str, Any] = {
        "lifecycle_state": lifecycle_state,
        "break_even_reached": be_reached,
        "first_break_even_time": be.get("be_t"),
        "be_bid": be.get("be_bid"),
        "be_yen": be.get("be_yen"),
        "u_bb_lower_break": bb_before_be,
        "u_bb_lower_break_time": bb.get("event_t"),
        "u_bb_lower_break_bar_time": bb.get("bar_t"),
        "bb_break_before_be": bb_before_be,
        "trigger_time": bb.get("event_t") if bb_before_be else None,
        "trigger_bar_time": bb.get("bar_t") if bb_before_be else None,
        "branch_u_exit_triggered": False,
        "close_at_trigger": bb.get("close"),
        "bb_lower_at_trigger": bb.get("bb_lower"),
        "quote_n": be.get("quote_n"),
    }
    treatment = dict(control)
    if bb_before_be and _finite(bb.get("event_t")):
        decision_t = float(bb["event_t"])
        leak["BRANCH_U_TRIGGER_N"] = int(leak.get("BRANCH_U_TRIGGER_N") or 0) + 1
        u["branch_u_exit_triggered"] = True
        if decision_t + 1e-12 < fill_t:
            leak["PRE_BAR_EXIT_N"] = int(leak.get("PRE_BAR_EXIT_N") or 0) + 1
        else:
            tech = first_causal_bid_evidence(
                board, meta, event_t=decision_t, fill_px=fill_px, sess_end=float(am_end), leak=leak
            )
            if tech.get("pre_decision"):
                pass
            elif not tech.get("miss"):
                seq = [
                    {"seq": 1, "event": "FILL", "t": fill_t, "price": fill_px},
                    {
                        "seq": 2,
                        "event": "U_BB_LOWER_BREAK",
                        "t": decision_t,
                        "bar_t": bb.get("bar_t"),
                        "close": bb.get("close"),
                        "bb_lower": bb.get("bb_lower"),
                    },
                    {"seq": 3, "event": TRIGGER_NAME, "reason": EXIT_REASON, "t": decision_t},
                    {
                        "seq": 4,
                        "event": "EXIT_BID",
                        "t": tech.get("exit_t"),
                        "bid": tech.get("exit_bid"),
                        "freshness_evidence": tech.get("freshness_evidence"),
                    },
                ]
                treatment = _pack_exit(
                    reason=EXIT_REASON,
                    fill_px=fill_px,
                    pack=tech,
                    leak=leak,
                    extra={
                        "trigger_event_time": decision_t,
                        "trigger_bar_time": bb.get("bar_t"),
                        "first_eligible_bid_time": tech.get("exit_t"),
                        "exit_event_sequence": seq,
                    },
                )
                if str(rec.get("fill_role") or "") == "CORE":
                    leak["BRANCH_U_CORE_EXIT_N"] = int(leak.get("BRANCH_U_CORE_EXIT_N") or 0) + 1
                elif str(rec.get("fill_role") or "") == "ADDED":
                    leak["BRANCH_U_ADDED_EXIT_N"] = int(leak.get("BRANCH_U_ADDED_EXIT_N") or 0) + 1
            else:
                leak["TECH_EXIT_BID_MISS_THEN_SESSION_N"] = int(leak.get("TECH_EXIT_BID_MISS_THEN_SESSION_N") or 0) + 1
                miss_seq = [
                    {"seq": 1, "event": "FILL", "t": fill_t, "price": fill_px},
                    {"seq": 2, "event": "U_BB_LOWER_BREAK", "t": decision_t, "bar_t": bb.get("bar_t")},
                    {"seq": 3, "event": TRIGGER_NAME, "reason": EXIT_REASON, "t": decision_t},
                    {"seq": 4, "event": "EXIT_BID_MISS_THEN_SESSION_CLOSE", "t": sess.get("exit_t"), "bid": sess.get("exit_bid")},
                ]
                treatment = dict(control)
                treatment["exit_event_sequence"] = miss_seq
                treatment["trigger_event_time"] = decision_t
                treatment["trigger_bar_time"] = bb.get("bar_t")
    if lifecycle_state == "PROVEN" and str(treatment.get("reason") or "") == EXIT_REASON:
        leak["U_EXIT_AFTER_PROVEN_N"] = int(leak.get("U_EXIT_AFTER_PROVEN_N") or 0) + 1
    rec["treatment_exit"] = treatment
    rec["branch_u"] = u
    return rec


def _leak0() -> dict[str, Any]:
    return {
        "VIRTUAL_FILL_N": 0,
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
        "K_SEARCH_N": 0,
        "BAR_COUNT_SEARCH_N": 0,
        "COMBINATION_SEARCH_N": 0,
        "PNL_SELECTION_N": 0,
        "GIVEBACK_SEARCH_N": 0,
        "BPS_THRESHOLD_SEARCH_N": 0,
        "WAIT_EXTENSION_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "SECOND_FALLBACK_N": 0,
        "FALLBACK_MARKET_N": 0,
        "TIME_STOP_N": 0,
        "EXIT_COMBINATION_N": 0,
        "MIXED_TF_RULE_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "LAST_QUOTE_CARRYBACK_TECH_N": 0,
        "VWAP_N": 0,
        "EXIT_PRICE_OPT_N": 0,
        "DELAY_OPT_N": 0,
        "RCI_EXIT_N": 0,
        "VOLUME_EXIT_N": 0,
        "MFE_EXIT_N": 0,
        "ALTERNATE_MECHANISM_N": 0,
        "BRANCH_P_TECH_EXIT_N": 0,
        "U_EXIT_AFTER_PROVEN_N": 0,
        "BB_DEF_DRIFT_N": 0,
        "BB_PARAM_CHANGE_N": 0,
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
        "BRANCH_U_EXIT_N": 0,
        "BRANCH_U_TRIGGER_N": 0,
        "BRANCH_U_CORE_EXIT_N": 0,
        "BRANCH_U_ADDED_EXIT_N": 0,
        "SESSION_CLOSE_EXIT_N": 0,
        "SESSION_CLOSE_MISS_N": 0,
        "EXIT_MISS_N": 0,
        "TECH_EXIT_BID_MISS_THEN_SESSION_N": 0,
        "HH_HL_INVENTED_N": 0,
    }


def replay_branch_u_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = _leak0()
    empty = {
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
    if not signals:
        return empty
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        needed = {_bare(r.get("symbol")) for r in signals if _bare(r.get("symbol"))}
        bufs = {s: _Buf() for s in needed}
        builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in needed}
        srcs = {s: [] for s in needed}
        clocks = {s: [] for s in needed}
        price_ages = {s: [] for s in needed}
        events_n = 0
        last_et: Optional[float] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in needed:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
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
                print(f"{day} branch-u stream kept={events_n} last_et={last_et}", flush=True)
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
            rec.pop("exit_eval", None)
            rec.pop("state_sequence", None)
            rec = attach_u_exits(
                rec,
                views[s],
                metas[s],
                (tf_by_sym.get(s) or {}).get("tf1") or {},
                am_end=am_end,
                leak=leak,
            )
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(
            f"{day} branch-u kept_events={events_n} signals={len(rows)} integ_fail={integ_fail} last_et={last_et}",
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


def save_branch_u_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim_rows = []
    for r in list(body.get("rows") or []):
        slim_rows.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "actual_filled": r.get("actual_filled"),
                "executable_signal": r.get("executable_signal"),
                "path_type": r.get("path_type"),
                "e4_filled": r.get("e4_filled"),
                "e4": r.get("e4"),
                "branch_u": r.get("branch_u"),
                "control_exit": r.get("control_exit"),
                "treatment_exit": r.get("treatment_exit"),
            }
        )
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
            "rows": slim_rows,
            "blocker": body.get("blocker"),
            "signal_n": body.get("signal_n"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_branch_u_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


def _smoke() -> None:
    t = np.asarray([10.0, 20.0, 30.0, 40.0], dtype=float)
    board = {
        "t": t,
        "bid": np.asarray([99.0, 99.0, 100.0, 101.0], dtype=float),
        "ask": np.asarray([100.0, 100.0, 101.0, 102.0], dtype=float),
        "bid_qty": np.full(4, 100.0),
        "ask_qty": np.full(4, 100.0),
        "executable": np.ones(4, dtype=bool),
        "special": np.zeros(4, dtype=bool),
        "fresh_sec": np.zeros(4, dtype=float),
    }
    meta = {"clock": t.copy()}
    leak: dict[str, Any] = {}
    be = walk_break_even(board, meta, fill_t=5.0, fill_px=100.0, sess_end=50.0, pullback_low=None, leak=leak)
    assert be["break_even_reached"] is True
    fin = np.asarray([12.0, 25.0, 45.0], dtype=float)
    tf1 = {
        "finalize_t": fin,
        "minute_epoch": np.asarray([0.0, 60.0, 120.0], dtype=float),
        "close": np.asarray([101.0, 90.0, 80.0], dtype=float),
        "bb_lower": np.asarray([95.0, 95.0, 95.0], dtype=float),
    }
    hit, ht = _first_bar_flag(tf1, start_t=5.0, end_t=float(be["be_t"]), pred=pred_bb_lower)
    bb = first_bb_lower_break(tf1, start_t=5.0, end_t=float(be["be_t"]))
    assert hit is True and bb["hit"] is True
    assert abs(float(ht) - float(bb["event_t"])) < 1e-12
    assert abs(float(ht) - 25.0) < 1e-12
    bb2 = first_bb_lower_break(tf1, start_t=5.0, end_t=20.0)
    assert bb2["hit"] is False
    assert int(SHARES) == int(V28_SHARES) == 100


_smoke()
assert abs(float(compute_pnl_yen_100(100.0, 100.0, side="long"))) < 1e-12

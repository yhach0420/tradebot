"""V28 sealed replay: V26 fills + ADDED-only 3m EMA persistence K=6 then first causal Bid1. CORE session-close only."""
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
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v26_harvest import joint_row
from research.simple_tech_redesign.v28_spec import (
    CANONICAL_FRESHNESS_SEC,
    MIN_BID_QTY,
    PERSISTENCE_K,
    SHARES,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V28_CACHE = RESEARCH_CACHE / "v28_fallback_3m_ema_persistence_exit"


def _empty_k6() -> dict[str, Any]:
    return {
        "observable": False,
        "post_fill_bar_n": 0,
        "loss_episode_n": 0,
        "k6_reached": False,
        "k6_recovered_before_trigger_n": 0,
        "k6_decision_t": None,
        "max_consecutive_loss_bars": 0,
    }


def eval_k6_persistence(ind: dict[str, np.ndarray], *, fill_t: float, k: int = PERSISTENCE_K) -> dict[str, Any]:
    """Consecutive completed-3m EMA9<=EMA21. Reset on EMA9>EMA21. First hit of K is the decision."""
    if int(k) != int(PERSISTENCE_K):
        raise ValueError("PERSISTENCE_K is frozen at 6")
    out = _empty_k6()
    fin = ind.get("finalize_t")
    ema9 = ind.get("ema9")
    ema21 = ind.get("ema21")
    n = int(fin.size) if fin is not None else 0
    if n <= 0 or ema9 is None or ema21 is None:
        return out
    j = int(np.searchsorted(fin, float(fill_t), side="right"))
    if j >= n:
        return out
    out["observable"] = True
    out["post_fill_bar_n"] = int(n - j)
    loss_count = 0
    in_loss = False
    episode_n = 0
    recovered = 0
    max_run = 0
    for i in range(j, n):
        if not _finite(fin[i]) or not _finite(ema9[i]) or not _finite(ema21[i]):
            continue
        e9 = float(ema9[i])
        e21 = float(ema21[i])
        if e9 > e21:
            if in_loss and 0 < loss_count < int(k):
                recovered += 1
            loss_count = 0
            in_loss = False
            continue
        if not in_loss:
            episode_n += 1
            in_loss = True
        loss_count += 1
        max_run = max(max_run, loss_count)
        if loss_count == int(k):
            out["k6_reached"] = True
            out["k6_decision_t"] = float(fin[i])
            break
    out["loss_episode_n"] = int(episode_n)
    out["k6_recovered_before_trigger_n"] = int(recovered)
    out["max_consecutive_loss_bars"] = int(max_run)
    return out


def _bid_ok(board: dict[str, np.ndarray], i: int) -> bool:
    """Fresh valid Bid1 with Bid1Qty>=100. Ask not required (V1R session-close Bid1 semantics)."""
    snap = _snap(board, i)
    if not bool(snap.get("executable")) or bool(snap.get("special")):
        return False
    fresh = snap.get("fresh_sec")
    if not _finite(fresh) or float(fresh) > float(CANONICAL_FRESHNESS_SEC) + 1e-12:
        return False
    if not _finite(snap.get("bid")) or float(snap["bid"]) <= 0.0:
        return False
    if not _finite(snap.get("bid_qty")) or float(snap["bid_qty"]) < float(MIN_BID_QTY) - 1e-12:
        return False
    return True


def _clock_ok(meta: dict[str, np.ndarray], i: int, ti: float, leak: dict[str, Any]) -> bool:
    clk_arr = meta.get("clock") if meta else None
    if clk_arr is None or int(getattr(clk_arr, "size", 0) or 0) <= i:
        return True
    if not _finite(clk_arr[i]):
        leak["BOARD_CLOCK_UNRESOLVED_N"] = int(leak.get("BOARD_CLOCK_UNRESOLVED_N") or 0) + 1
        return False
    if float(clk_arr[i]) > float(ti) + 1e-12:
        leak["FUTURE_TIMESTAMP_CARRYBACK_N"] = int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0) + 1
        return False
    return True


def first_causal_bid(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    event_t: float,
    fill_px: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out = {"exit_t": None, "exit_bid": None, "miss": True, "pre_decision": False}
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
        return {"exit_t": ti, "exit_bid": bid, "miss": False, "pre_decision": False}
    return out


def last_session_bid(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    sess_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    """V1R canonical SESSION_CLOSE: last valid executable Bid1 at-or-before AM end. No fill-price fallback."""
    t = board.get("t")
    out = {"exit_t": None, "exit_bid": None, "miss": True}
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
        return {"exit_t": ti, "exit_bid": bid, "miss": False}
    leak["SESSION_CLOSE_MISS_N"] = int(leak.get("SESSION_CLOSE_MISS_N") or 0) + 1
    return out


def _pack_exit(*, reason: str, fill_px: float, pack: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    miss = bool(pack.get("miss"))
    exit_t = pack.get("exit_t")
    exit_bid = pack.get("exit_bid")
    yen = None
    if (not miss) and _finite(exit_bid) and _finite(fill_px):
        yen = float(compute_pnl_yen_100(float(fill_px), float(exit_bid), side="long"))
    if miss:
        leak["EXIT_MISS_N"] = int(leak.get("EXIT_MISS_N") or 0) + 1
    if reason == "TECHNICAL_EXIT":
        leak["TECH_EXIT_N"] = int(leak.get("TECH_EXIT_N") or 0) + 1
    elif reason == "SESSION_CLOSE":
        leak["SESSION_CLOSE_EXIT_N"] = int(leak.get("SESSION_CLOSE_EXIT_N") or 0) + 1
    return {
        "reason": reason,
        "exit_t": float(exit_t) if _finite(exit_t) else None,
        "exit_bid": float(exit_bid) if _finite(exit_bid) else None,
        "pnl_yen_100": yen,
        "miss": bool(miss),
        "shares": int(SHARES),
    }


def attach_exits(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    tf3: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec["k6"] = _empty_k6()
    rec["control_exit"] = None
    rec["treatment_exit"] = None
    if not rec.get("actual_filled") or not _finite(rec.get("fill_t")) or not _finite(rec.get("fill_price")):
        return rec
    fill_t = float(rec["fill_t"])
    fill_px = float(rec["fill_price"])
    role = str(rec.get("fill_role") or "")
    rec["k6"] = eval_k6_persistence(tf3, fill_t=fill_t, k=int(PERSISTENCE_K))
    sess = last_session_bid(board, meta, fill_t=fill_t, sess_end=float(am_end), leak=leak)
    control = _pack_exit(reason="SESSION_CLOSE", fill_px=fill_px, pack=sess, leak=leak)
    rec["control_exit"] = control
    if role == "CORE":
        rec["treatment_exit"] = dict(control)
        if rec["k6"].get("k6_reached"):
            leak["CORE_K6_WOULD_FIRE_N"] = int(leak.get("CORE_K6_WOULD_FIRE_N") or 0) + 1
        return rec
    if role != "ADDED":
        rec["treatment_exit"] = dict(control)
        return rec
    k6 = rec["k6"]
    if k6.get("k6_reached") and _finite(k6.get("k6_decision_t")):
        leak["K6_REACHED_N"] = int(leak.get("K6_REACHED_N") or 0) + 1
        decision_t = float(k6["k6_decision_t"])
        if decision_t + 1e-12 < fill_t:
            leak["PRE_BAR_EXIT_N"] = int(leak.get("PRE_BAR_EXIT_N") or 0) + 1
            rec["treatment_exit"] = dict(control)
            return rec
        tech = first_causal_bid(
            board, meta, event_t=decision_t, fill_px=fill_px, sess_end=float(am_end), leak=leak
        )
        if tech.get("pre_decision"):
            rec["treatment_exit"] = dict(control)
            return rec
        if not tech.get("miss"):
            rec["treatment_exit"] = _pack_exit(reason="TECHNICAL_EXIT", fill_px=fill_px, pack=tech, leak=leak)
            return rec
        leak["TECH_EXIT_BID_MISS_THEN_SESSION_N"] = int(leak.get("TECH_EXIT_BID_MISS_THEN_SESSION_N") or 0) + 1
    rec["treatment_exit"] = dict(control)
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
        "WAIT_EXTENSION_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "SECOND_FALLBACK_N": 0,
        "FALLBACK_MARKET_N": 0,
        "TIME_STOP_N": 0,
        "EXIT_COMBINATION_N": 0,
        "MIXED_TF_RULE_N": 0,
        "CORE_TECH_EXIT_N": 0,
        "PRE_DECISION_EXIT_N": 0,
        "PRE_BAR_EXIT_N": 0,
        "LAST_QUOTE_CARRYBACK_TECH_N": 0,
        "VWAP_N": 0,
        "EXIT_PRICE_OPT_N": 0,
        "DELAY_OPT_N": 0,
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
        "TECH_EXIT_N": 0,
        "SESSION_CLOSE_EXIT_N": 0,
        "SESSION_CLOSE_MISS_N": 0,
        "EXIT_MISS_N": 0,
        "K6_REACHED_N": 0,
        "TECH_EXIT_BID_MISS_THEN_SESSION_N": 0,
        "CORE_K6_WOULD_FIRE_N": 0,
        "BB_EXIT_N": 0,
        "RCI_EXIT_N": 0,
        "VOLUME_EXIT_N": 0,
        "MFE_EXIT_N": 0,
    }


def replay_policy_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = _leak0()
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
                print(f"{day} v28 policy stream kept={events_n} last_et={last_et}", flush=True)
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
            rec = attach_exits(
                rec,
                views[s],
                metas[s],
                (tf_by_sym.get(s) or {}).get("tf3") or {},
                am_end=am_end,
                leak=leak,
            )
            if str(rec.get("fill_role") or "") == "CORE" and str((rec.get("treatment_exit") or {}).get("reason") or "") == "TECHNICAL_EXIT":
                leak["CORE_TECH_EXIT_N"] = int(leak.get("CORE_TECH_EXIT_N") or 0) + 1
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(
            f"{day} v28 policy kept_events={events_n} signals={len(rows)} "
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


def save_v28_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v28_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


def _smoke_k6() -> None:
    fin = np.asarray([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0], dtype=float)
    ema9 = np.asarray([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], dtype=float)
    ema21 = np.asarray([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0], dtype=float)
    pack = eval_k6_persistence({"finalize_t": fin, "ema9": ema9, "ema21": ema21}, fill_t=5.0, k=6)
    assert pack["k6_reached"] is True
    assert abs(float(pack["k6_decision_t"]) - 70.0) < 1e-12
    assert int(pack["loss_episode_n"]) == 1
    ema9b = np.asarray([0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=float)
    ema21b = np.asarray([1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=float)
    p2 = eval_k6_persistence({"finalize_t": fin, "ema9": ema9b, "ema21": ema21b}, fill_t=5.0, k=6)
    assert p2["k6_reached"] is False
    assert int(p2["k6_recovered_before_trigger_n"]) == 1
    assert int(p2["loss_episode_n"]) == 2
    assert int(p2["max_consecutive_loss_bars"]) == 5


_smoke_k6()
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert int(PERSISTENCE_K) == 6
assert int(SHARES) == 100

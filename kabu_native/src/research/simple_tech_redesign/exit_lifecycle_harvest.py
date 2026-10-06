"""Sealed lifecycle harvest: first net executable BE (yen>=0), then U/P thesis states. No EXIT."""
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
from research.simple_tech_entry_family import PULLBACK_LOOKBACK, RCI_CROSS_LEVEL
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators, pullback_setup, reversal_rci, trend_up
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import NAN, _finite, corrected_board_freshness
from research.simple_tech_redesign.v26_harvest import joint_row
from research.simple_tech_redesign.v27_harvest import primitive_known
from research.simple_tech_redesign.v28_harvest import _bid_ok, _clock_ok
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_live_dual_lane import session_end_for_position

LIFECYCLE_CACHE = RESEARCH_CACHE / "exit_lifecycle_branch_rca"


def _last_i_at_or_before(ind: dict[str, np.ndarray], t_cut: float) -> Optional[int]:
    fin = ind.get("finalize_t")
    if fin is None or int(fin.size) == 0 or not _finite(t_cut):
        return None
    i = int(np.searchsorted(fin, float(t_cut), side="right") - 1)
    return int(i) if i >= 0 else None


def _arr_at(ind: dict[str, np.ndarray], key: str, i: int) -> Optional[float]:
    arr = ind.get(key)
    if arr is None or i < 0 or i >= int(arr.size) or not _finite(arr[i]):
        return None
    return float(arr[i])


def _pullback_low_px(ind: dict[str, np.ndarray], i: int) -> Optional[float]:
    if i < 0:
        return None
    lo = None
    start = max(0, int(i) - int(PULLBACK_LOOKBACK) + 1)
    for k in range(start, int(i) + 1):
        v = _arr_at(ind, "low", k)
        if v is None:
            continue
        if lo is None or float(v) < float(lo):
            lo = float(v)
    return lo


def _hh_hl(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
    if i < 1:
        return None
    hi, hip = _arr_at(ind, "high", i), _arr_at(ind, "high", i - 1)
    lo, lop = _arr_at(ind, "low", i), _arr_at(ind, "low", i - 1)
    if None in (hi, hip, lo, lop):
        return None
    return bool(float(hi) >= float(hip) and float(lo) >= float(lop))


def walk_break_even(
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    pullback_low: Optional[float],
    leak: dict[str, Any],
) -> dict[str, Any]:
    t = board.get("t")
    out = {
        "break_even_reached": False,
        "be_t": None,
        "be_bid": None,
        "be_yen": None,
        "peak_yen": None,
        "peak_t": None,
        "quote_n": 0,
        "pullback_low_bid_break": False,
        "pullback_low_break_t": None,
    }
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return out
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    peak = peak_t = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t) or ti > float(sess_end) + 1e-12:
            if ti > float(sess_end) + 1e-12:
                break
            continue
        if not _clock_ok(meta, i, ti, leak) or not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        if bid <= 0.0:
            continue
        yen = float(compute_pnl_yen_100(float(fill_px), bid, side="long"))
        out["quote_n"] = int(out["quote_n"]) + 1
        if peak is None or yen > float(peak):
            peak, peak_t = yen, ti
        if pullback_low is not None and (not out["pullback_low_bid_break"]) and bid < float(pullback_low) - 1e-12:
            out["pullback_low_bid_break"] = True
            out["pullback_low_break_t"] = ti
        if yen >= 0.0:
            out["break_even_reached"] = True
            out["be_t"] = ti
            out["be_bid"] = bid
            out["be_yen"] = yen
            break
    out["peak_yen"] = peak
    out["peak_t"] = peak_t
    if out["break_even_reached"] and out.get("pullback_low_break_t") is not None:
        if float(out["pullback_low_break_t"]) + 1e-12 >= float(out["be_t"]):
            out["pullback_low_bid_break"] = False
            out["pullback_low_break_t"] = None
    return out


def _first_bar_flag(tf: dict[str, np.ndarray], *, start_t: float, end_t: Optional[float], pred) -> tuple[bool, Optional[float]]:
    fin = tf.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    if n <= 0:
        return False, None
    j = int(np.searchsorted(fin, float(start_t), side="right"))
    for i in range(j, n):
        ft = float(fin[i]) if _finite(fin[i]) else None
        if ft is None:
            continue
        if end_t is not None and ft + 1e-12 >= float(end_t):
            break
        if pred(tf, int(i)) is True:
            return True, ft
    return False, None


def eval_lifecycle(
    tf1: dict[str, np.ndarray],
    tf3: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    t0: float,
    fill_t: float,
    fill_px: float,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    i_sig = _last_i_at_or_before(tf1, float(t0))
    pullback_low = _pullback_low_px(tf1, int(i_sig)) if i_sig is not None else None
    entry = {
        "signal_i": i_sig,
        "trend_up": bool(trend_up(tf1, int(i_sig))) if i_sig is not None else None,
        "pullback_setup": bool(pullback_setup(tf1, int(i_sig))) if i_sig is not None else None,
        "reversal_rci": bool(reversal_rci(tf1, int(i_sig))) if i_sig is not None else None,
        "pullback_low": pullback_low,
        "vwap": _arr_at(tf1, "vwap", int(i_sig)) if i_sig is not None else None,
        "ema9": _arr_at(tf1, "ema9", int(i_sig)) if i_sig is not None else None,
        "ema21": _arr_at(tf1, "ema21", int(i_sig)) if i_sig is not None else None,
        "rci9": _arr_at(tf1, "rci9", int(i_sig)) if i_sig is not None else None,
        "close": _arr_at(tf1, "close", int(i_sig)) if i_sig is not None else None,
        "bb_lower": _arr_at(tf1, "bb_lower", int(i_sig)) if i_sig is not None else None,
        "fill_price": float(fill_px),
        "fill_below_pullback_low": bool(pullback_low is not None and float(fill_px) < float(pullback_low) - 1e-12),
    }
    be = walk_break_even(
        board, meta, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=float(am_end), pullback_low=pullback_low, leak=leak
    )
    u_end = float(be["be_t"]) if be.get("break_even_reached") else None
    u: dict[str, Any] = {
        "U_NEVER_BREAK_EVEN": (not bool(be.get("break_even_reached"))),
        "U_PULLBACK_LOW_BID_BREAK": bool(be.get("pullback_low_bid_break")),
        "U_PULLBACK_LOW_BID_BREAK_t": be.get("pullback_low_break_t"),
    }

    def pred_bb_lower(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
        cl, bb = _arr_at(ind, "close", i), _arr_at(ind, "bb_lower", i)
        return None if cl is None or bb is None else bool(cl < bb)

    def pred_rci_os(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
        r = _arr_at(ind, "rci9", i)
        return None if r is None else bool(r <= float(RCI_CROSS_LEVEL))

    def pred_vwap(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
        cl, vw = _arr_at(ind, "close", i), _arr_at(ind, "vwap", i)
        return None if cl is None or vw is None else bool(cl < vw)

    def pred_hh_lost(ind: dict[str, np.ndarray], i: int) -> Optional[bool]:
        v = _hh_hl(ind, i)
        return None if v is None else (not v)

    for key, pred in (
        ("U_TREND_LOST", lambda ind, i: not trend_up(ind, i)),
        ("U_EMA_STRUCTURE_LOSS", lambda ind, i: primitive_known("A_EMA_STRUCTURE_LOSS", ind, i)),
        ("U_BB_LOWER_BREAK", pred_bb_lower),
        ("U_RCI_RE_OVERSOLD", pred_rci_os),
        ("U_VWAP_CLOSE_LOSS", pred_vwap),
        ("U_HH_HL_LOST", pred_hh_lost),
    ):
        hit, ht = _first_bar_flag(tf1, start_t=float(fill_t), end_t=u_end, pred=pred)
        u[key] = bool(hit)
        u[f"{key}_t"] = ht

    p: dict[str, Any] = {"observable": bool(be.get("break_even_reached"))}
    keys_tf = (
        ("P_TREND_LOST_1M", lambda ind, i: not trend_up(ind, i), tf1),
        ("P_EMA_STRUCTURE_LOSS_3M", lambda ind, i: primitive_known("A_EMA_STRUCTURE_LOSS", ind, i), tf3),
        ("P_PRICE_STRUCTURE_LOSS_3M", lambda ind, i: primitive_known("C_PRICE_STRUCTURE_LOSS", ind, i), tf3),
        ("P_BB_STRUCTURE_LOSS_3M", lambda ind, i: primitive_known("D_BB_STRUCTURE_LOSS", ind, i), tf3),
        ("P_RCI_ROLLOVER_3M", lambda ind, i: primitive_known("E_RCI_ROLLOVER", ind, i), tf3),
        ("P_VWAP_CLOSE_LOSS_1M", pred_vwap, tf1),
    )
    if be.get("break_even_reached"):
        for key, pred, tf in keys_tf:
            hit, ht = _first_bar_flag(tf, start_t=float(be["be_t"]), end_t=None, pred=pred)
            p[key] = bool(hit)
            p[f"{key}_t"] = ht
    else:
        for key, _pred, _tf in keys_tf:
            p[key] = None
            p[f"{key}_t"] = None
    return {
        "branch": "P" if be.get("break_even_reached") else "U",
        "entry": entry,
        "break_even_reached": bool(be.get("break_even_reached")),
        "be_t": be.get("be_t"),
        "be_bid": be.get("be_bid"),
        "be_yen": be.get("be_yen"),
        "peak_yen": be.get("peak_yen"),
        "peak_t": be.get("peak_t"),
        "quote_n": be.get("quote_n"),
        "shares": int(SHARES),
        "u": u,
        "p": p,
    }


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
        "EXIT_POLICY_N": 0,
        "WAIT_EXTENSION_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "SECOND_FALLBACK_N": 0,
        "FALLBACK_MARKET_N": 0,
        "TIME_STOP_N": 0,
        "EXIT_COMBINATION_N": 0,
        "MIXED_TF_RULE_N": 0,
        "HH_HL_INVENTED_N": 0,
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


def replay_lifecycle_day(payload: dict[str, Any]) -> dict[str, Any]:
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
                print(f"{day} lifecycle stream kept={events_n} last_et={last_et}", flush=True)
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
            rec["lifecycle"] = None
            if rec.get("actual_filled") and _finite(rec.get("fill_t")) and _finite(rec.get("fill_price")) and _finite(rec.get("t0")):
                rec["lifecycle"] = eval_lifecycle(
                    (tf_by_sym.get(s) or {}).get("tf1") or {},
                    (tf_by_sym.get(s) or {}).get("tf3") or {},
                    views[s],
                    metas[s],
                    t0=float(rec["t0"]),
                    fill_t=float(rec["fill_t"]),
                    fill_px=float(rec["fill_price"]),
                    am_end=am_end,
                    leak=leak,
                )
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(f"{day} lifecycle kept_events={events_n} signals={len(rows)} integ_fail={integ_fail} last_et={last_et}", flush=True)
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
        return {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}", "elapsed_sec": round(time.perf_counter() - t0w, 3)}


def save_lifecycle_day_cache(path: Path, body: dict[str, Any]) -> None:
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
                "lifecycle": r.get("lifecycle"),
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


def load_lifecycle_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
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
    be = walk_break_even(board, meta, fill_t=5.0, fill_px=100.0, sess_end=50.0, pullback_low=98.0, leak=leak)
    assert be["break_even_reached"] is True
    assert abs(float(be["be_yen"])) < 1e-12
    be2 = walk_break_even(
        {**board, "bid": np.asarray([99.0, 99.0, 99.0, 99.0], dtype=float)},
        meta, fill_t=5.0, fill_px=100.0, sess_end=50.0, pullback_low=98.5, leak=leak,
    )
    assert be2["break_even_reached"] is False
    be3 = walk_break_even(
        {**board, "bid": np.asarray([97.0, 97.0, 97.0, 97.0], dtype=float)},
        meta, fill_t=5.0, fill_px=100.0, sess_end=50.0, pullback_low=98.0, leak=leak,
    )
    assert be3["pullback_low_bid_break"] is True
    assert abs(float(compute_pnl_yen_100(100.0, 100.0, side="long"))) < 1e-12


_smoke()
assert int(SHARES) == 100


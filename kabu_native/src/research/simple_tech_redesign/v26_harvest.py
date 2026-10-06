"""V26 sealed replay: frozen E4 then one Ask1 marketable-limit at t0+5. Post-fill 1m/3m/5m primitives. No chase."""
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
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _board_row, load_day_cache
from research.simple_tech_entry_family.stages import attach_indicators
from research.simple_tech_entry_family.v3_harvest import _bid_ok, _last_bid_before
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import V12_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import (
    NAN,
    _board_quote_t,
    _finite,
    _ingress_epoch,
    corrected_board_freshness,
    corrected_row,
)
from research.simple_tech_redesign.v26_spec import (
    CANONICAL_FRESHNESS_SEC,
    E4_WAIT_BUDGET_SEC,
    PATH_HORIZONS_SEC,
    PRIMITIVES,
    TF_TO_INTERNAL,
    TF_WIDTH_SEC,
    TFS,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V26_CACHE = RESEARCH_CACHE / "v26_joint_coverage_technical_exit_rca"


def _bps(num: Any, den: Any) -> Optional[float]:
    if not _finite(num) or not _finite(den) or float(den) <= 0.0:
        return None
    return (float(num) / float(den) - 1.0) * 10000.0


def simulate_ask_fallback(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "eligible": False,
        "filled": False,
        "reason": None,
        "limit_price": None,
        "fill_price": None,
        "fill_t": None,
        "snap_t": None,
        "ask": None,
        "ask_qty": None,
        "fresh_at_tfb_sec": None,
        "clock_source": None,
        "evidence": None,
    }
    rec["ask_fallback"] = pack
    if rec.get("e4_filled"):
        pack["reason"] = "CORE_E4_FILL"
        return rec
    if not rec.get("executable_signal"):
        pack["reason"] = "NOT_EVALUABLE"
        return rec
    t0 = float(rec["t0"])
    t_fb = float(t0) + float(E4_WAIT_BUDGET_SEC)
    if t_fb > float(am_end) + 1e-12:
        pack["reason"] = "SESSION_END_BEFORE_FALLBACK"
        return rec
    t = board.get("t")
    if t is None or int(t.size) == 0:
        pack["reason"] = "NO_BOARD"
        return rec
    i = int(np.searchsorted(t, t_fb, side="right") - 1)
    if i < 0:
        pack["reason"] = "NO_BOARD"
        return rec
    ti = float(t[i])
    if ti > t_fb + 1e-12:
        leak["FUTURE_BOARD_CARRYBACK_N"] = int(leak.get("FUTURE_BOARD_CARRYBACK_N") or 0) + 1
        leak["FUTURE_BOARD_N"] = int(leak.get("FUTURE_BOARD_N") or 0) + 1
        pack["reason"] = "FUTURE_BOARD"
        return rec
    clk = float(meta["clock"][i]) if _finite(meta["clock"][i]) else None
    source = str(meta["source"][i] or "UNRESOLVED")
    pack["snap_t"] = ti
    pack["clock_source"] = source
    if clk is None or source == "UNRESOLVED":
        leak["BOARD_CLOCK_UNRESOLVED_N"] = int(leak.get("BOARD_CLOCK_UNRESOLVED_N") or 0) + 1
        pack["reason"] = "BOARD_CLOCK_UNRESOLVED"
        return rec
    if float(clk) > t_fb + 1e-12:
        leak["FUTURE_TIMESTAMP_CARRYBACK_N"] = int(leak.get("FUTURE_TIMESTAMP_CARRYBACK_N") or 0) + 1
        pack["reason"] = "FUTURE_CLOCK"
        return rec
    age = float(t_fb) - float(clk)
    if age < 0.0:
        age = 0.0
    pack["fresh_at_tfb_sec"] = float(age)
    exec_arr = board.get("executable")
    if exec_arr is not None and int(getattr(exec_arr, "size", 0) or 0) > i and not bool(exec_arr[i]):
        pack["reason"] = "NOT_EXECUTABLE"
        return rec
    if bool(board["special"][i]):
        pack["reason"] = "SPECIAL"
        return rec
    if age > float(CANONICAL_FRESHNESS_SEC) + 1e-12:
        pack["reason"] = "STALE_AT_T0_PLUS_5"
        return rec
    ask = float(board["ask"][i])
    qty = float(board["ask_qty"][i])
    pack["ask"] = ask if ask == ask else None
    pack["ask_qty"] = qty if qty == qty else None
    if not (ask == ask) or ask <= 0.0:
        pack["reason"] = "NO_ASK1"
        return rec
    if not (qty == qty) or qty < float(MIN_QTY) - 1e-12:
        pack["reason"] = "ASK1_QTY_LT_100"
        return rec
    pack["eligible"] = True
    pack["filled"] = True
    pack["limit_price"] = float(ask)
    pack["fill_price"] = float(ask)
    pack["fill_t"] = float(t_fb)
    pack["evidence"] = "ASK_CROSS_CONSERVATIVE"
    pack["reason"] = None
    leak["ASK_FALLBACK_ELIGIBLE_N"] = int(leak.get("ASK_FALLBACK_ELIGIBLE_N") or 0) + 1
    leak["ASK_FALLBACK_FILL_N"] = int(leak.get("ASK_FALLBACK_FILL_N") or 0) + 1
    leak["ASK_CROSS_FILL_N"] = int(leak.get("ASK_CROSS_FILL_N") or 0) + 1
    return rec


def _bid_excursions(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    path_end: float,
) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0 or not _finite(fill_px) or float(fill_px) <= 0.0:
        return None, None
    i0 = int(np.searchsorted(t, float(fill_t), side="right"))
    mfe = mae = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(fill_t) + 1e-12:
            continue
        if ti > float(path_end) + 1e-12:
            break
        if not _bid_ok(board, i):
            continue
        bps = (float(board["bid"][i]) / float(fill_px) - 1.0) * 10000.0
        mfe = bps if mfe is None or bps > mfe else mfe
        mae = bps if mae is None or bps < mae else mae
    return mfe, mae


def attach_fill_paths(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    am_end: float,
) -> dict[str, Any]:
    path: dict[str, Any] = {}
    for h in PATH_HORIZONS_SEC:
        hid = int(h)
        raw_mark = float(fill_t) + float(h)
        mark_t = min(raw_mark, float(am_end))
        clamped = bool(raw_mark > float(am_end) + 1e-12)
        bid, _bt = _last_bid_before(board, t0=float(fill_t), mark_t=float(mark_t))
        mfe, mae = _bid_excursions(board, fill_t=float(fill_t), fill_px=float(fill_px), path_end=float(mark_t))
        path[f"bid_markout_{hid}"] = _bps(bid, fill_px)
        path[f"mfe_{hid}"] = mfe
        path[f"mae_{hid}"] = mae
        path[f"clamped_{hid}"] = clamped
    mfe600, mae600 = _bid_excursions(
        board, fill_t=float(fill_t), fill_px=float(fill_px), path_end=min(float(fill_t) + 600.0, float(am_end))
    )
    path["mfe"] = mfe600
    path["mae"] = mae600
    rec["fill_path"] = path
    rec["path_type"] = classify_path_type(path)
    return rec


def classify_path_type(path: dict[str, Any]) -> str:
    m60 = path.get("bid_markout_60")
    m180 = path.get("bid_markout_180")
    m300 = path.get("bid_markout_300")
    m600 = path.get("bid_markout_600")
    if not any(_finite(v) for v in (m60, m180, m300, m600)):
        return "OTHER"
    later_pos = (_finite(m300) and float(m300) > 0.0) or (_finite(m600) and float(m600) > 0.0)
    later_neg = (_finite(m300) and float(m300) < 0.0) or (_finite(m600) and float(m600) < 0.0)
    if _finite(m60) and float(m60) < 0.0 and later_pos:
        return "DIP_THEN_RECOVERY"
    if _finite(m60) and float(m60) >= 0.0 and later_neg:
        return "PROFIT_THEN_FAILURE"
    if _finite(m60) and float(m60) >= 0.0 and (not _finite(m180) or float(m180) >= 0.0) and (
        not _finite(m300) or float(m300) >= 0.0
    ):
        return "GOOD_CONTINUATION"
    if _finite(m60) and float(m60) < 0.0 and (not _finite(m180) or float(m180) < 0.0) and (not later_pos):
        return "EARLY_FAILURE"
    return "OTHER"


def _ok(v: Any) -> bool:
    return _finite(v)


def primitive_true(pid: str, ind: dict[str, np.ndarray], i: int) -> bool:
    if i < 0:
        return False
    close = float(ind["close"][i]) if _ok(ind["close"][i]) else None
    ema9 = float(ind["ema9"][i]) if _ok(ind["ema9"][i]) else None
    ema21 = float(ind["ema21"][i]) if _ok(ind["ema21"][i]) else None
    bb_mid = float(ind["bb_mid"][i]) if _ok(ind["bb_mid"][i]) else None
    rci = float(ind["rci9"][i]) if _ok(ind["rci9"][i]) else None
    vol = float(ind["volume"][i]) if _ok(ind["volume"][i]) else None
    if pid == "A_EMA_STRUCTURE_LOSS":
        return ema9 is not None and ema21 is not None and float(ema9) <= float(ema21)
    if pid == "B_EMA_SHORT_SLOPE_LOSS":
        if i < 1:
            return False
        prev = float(ind["ema9"][i - 1]) if _ok(ind["ema9"][i - 1]) else None
        return ema9 is not None and prev is not None and float(ema9) < float(prev)
    if pid == "C_PRICE_STRUCTURE_LOSS":
        return close is not None and ema9 is not None and float(close) < float(ema9)
    if pid == "D_BB_STRUCTURE_LOSS":
        return close is not None and bb_mid is not None and float(close) < float(bb_mid)
    if pid == "E_RCI_ROLLOVER":
        if i < 1:
            return False
        prev = float(ind["rci9"][i - 1]) if _ok(ind["rci9"][i - 1]) else None
        return rci is not None and prev is not None and float(rci) < float(prev)
    if pid == "F_VOLUME_DETERIORATION":
        if i < 1:
            return False
        prev_v = float(ind["volume"][i - 1]) if _ok(ind["volume"][i - 1]) else None
        return (
            close is not None
            and ema9 is not None
            and float(close) > float(ema9)
            and vol is not None
            and prev_v is not None
            and float(vol) < float(prev_v)
        )
    return False


def eval_primitives(by_tf: dict[str, dict[str, np.ndarray]], *, fill_t: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
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
        pack: dict[str, Any] = {"post_fill_bar_n": int(post_n)}
        for pid in PRIMITIVES:
            fired = False
            fire_t = None
            bars_to_fire = None
            observable = bool(first_i is not None and post_n > 0)
            if first_i is not None:
                for k, i in enumerate(range(int(first_i), n)):
                    if primitive_true(pid, ind, int(i)):
                        fired = True
                        fire_t = float(fin[i])
                        bars_to_fire = int(k + 1)
                        break
            pack[pid] = {
                "fired": bool(fired),
                "fire_t": fire_t,
                "bars_to_fire": bars_to_fire,
                "observable": bool(observable),
            }
        out[tf] = pack
    return out


def joint_row(
    sig: dict[str, Any],
    board: dict[str, np.ndarray],
    meta: dict[str, np.ndarray],
    by_tf: dict[str, dict[str, np.ndarray]],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec = corrected_row(sig, board, meta, am_end=am_end, leak=leak)
    rec = simulate_ask_fallback(rec, board, meta, am_end=am_end, leak=leak)
    rec["fill_role"] = None
    rec["actual_filled"] = False
    rec["fill_t"] = None
    rec["fill_price"] = None
    rec["fill_path"] = None
    rec["path_type"] = None
    rec["exit_eval"] = None
    if rec.get("e4_filled"):
        e4 = dict(rec.get("e4") or {})
        rec["fill_role"] = "CORE"
        rec["actual_filled"] = True
        rec["fill_t"] = e4.get("fill_t")
        rec["fill_price"] = e4.get("fill_price")
    elif bool((rec.get("ask_fallback") or {}).get("filled")):
        fb = dict(rec.get("ask_fallback") or {})
        rec["fill_role"] = "ADDED"
        rec["actual_filled"] = True
        rec["fill_t"] = fb.get("fill_t")
        rec["fill_price"] = fb.get("fill_price")
    if rec.get("actual_filled") and _finite(rec.get("fill_t")) and _finite(rec.get("fill_price")):
        attach_fill_paths(
            rec,
            board,
            fill_t=float(rec["fill_t"]),
            fill_px=float(rec["fill_price"]),
            am_end=am_end,
        )
        rec["exit_eval"] = eval_primitives(by_tf, fill_t=float(rec["fill_t"]))
    rec["PATH_USED_FOR_LIVE_EXIT"] = False
    rec["PNL_EVAL"] = False
    rec["VIRTUAL_FILL"] = False
    rec.pop("exec", None)
    rec.pop("state_t0", None)
    rec.pop("state_path", None)
    return rec


def replay_joint_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "WAIT_EXTENSION_N": 0,
        "REPRICE_N": 0,
        "CHASE_N": 0,
        "SECOND_FALLBACK_N": 0,
        "FALLBACK_MARKET_N": 0,
        "TIME_STOP_N": 0,
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
                print(f"{day} v26 joint stream kept={events_n} last_et={last_et}", flush=True)
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
            rows.append(
                joint_row(
                    sig,
                    views[s],
                    metas[s],
                    tf_by_sym.get(s) or {},
                    am_end=am_end,
                    leak=leak,
                )
            )
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(
            f"{day} v26 joint kept_events={events_n} signals={len(rows)} "
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


def save_v26_day_cache(path: Path, body: dict[str, Any]) -> None:
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


def load_v26_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


assert abs(float(BOARD_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert tuple(TFS) == ("1m", "3m", "5m")
assert TF_WIDTH_SEC["5m"] == 300.0

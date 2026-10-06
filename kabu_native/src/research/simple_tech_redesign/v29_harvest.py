"""V29 sealed replay: first 3m EMA damage onset, then causal reclaim/price/volume sequences. No EXIT. No K selection."""
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
from research.simple_tech_redesign.v26_harvest import joint_row
from research.simple_tech_redesign.v27_harvest import primitive_known
from research.simple_tech_redesign.v28_harvest import eval_k6_persistence
from research.simple_tech_redesign.v28_spec import PERSISTENCE_K
from small_paper.v1r_live_dual_lane import session_end_for_position

V29_CACHE = RESEARCH_CACHE / "v29_terminal_failure_sequence_rca"


def _last_quote(board: dict[str, np.ndarray], *, t: float) -> tuple[Optional[float], Optional[float]]:
    arr = board.get("t")
    if arr is None or int(arr.size) == 0 or not _finite(t):
        return None, None
    i = int(np.searchsorted(arr, float(t), side="right") - 1)
    if i < 0:
        return None, None
    ti = float(arr[i])
    if ti > float(t) + 1e-12:
        return None, None
    bid = float(board["bid"][i]) if _finite(board["bid"][i]) else None
    ask = float(board["ask"][i]) if _finite(board["ask"][i]) else None
    return bid, ask


def _bar_minute(tf3: dict[str, np.ndarray], i: int) -> Optional[float]:
    arr = tf3.get("minute_epoch")
    if arr is None or i < 0 or i >= int(arr.size):
        return None
    return float(arr[i]) if _finite(arr[i]) else None


def eval_post_damage(
    tf3: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
) -> dict[str, Any]:
    empty = {
        "damage_onset": False,
        "onset_t": None,
        "onset_i": None,
        "post_onset_bar_n": 0,
        "events": [],
        "family_a": None,
        "family_b": None,
        "family_c": None,
        "reclaimed": False,
        "reloss_after_reclaim": False,
        "k6_reached": False,
        "k6_recovered_before_trigger_n": 0,
        "loss_episode_n": 0,
    }
    fin = tf3.get("finalize_t")
    n = int(fin.size) if fin is not None else 0
    if n <= 0:
        return empty
    j = int(np.searchsorted(fin, float(fill_t), side="right"))
    if j >= n:
        return empty
    onset_i = None
    for i in range(j, n):
        known = primitive_known("A_EMA_STRUCTURE_LOSS", tf3, int(i))
        if known is True:
            onset_i = int(i)
            break
    k6 = eval_k6_persistence(tf3, fill_t=float(fill_t), k=int(PERSISTENCE_K))
    if onset_i is None:
        empty["k6_reached"] = bool(k6.get("k6_reached"))
        empty["k6_recovered_before_trigger_n"] = int(k6.get("k6_recovered_before_trigger_n") or 0)
        empty["loss_episode_n"] = int(k6.get("loss_episode_n") or 0)
        return empty

    events: list[dict[str, Any]] = []
    ema_prev: Optional[bool] = None
    saw_reclaim = False
    saw_reloss = False
    price_lost_seen = False
    price_reclaim_after_lost = False
    price_reloss_after_reclaim = False
    price_ok_after_lost = False
    reclaim_attempt = False
    vol_det_on_attempt = False
    for idx, i in enumerate(range(onset_i, n)):
        ema_loss = primitive_known("A_EMA_STRUCTURE_LOSS", tf3, int(i))
        price_loss = primitive_known("C_PRICE_STRUCTURE_LOSS", tf3, int(i))
        vol_det = primitive_known("F_VOLUME_DETERIORATION", tf3, int(i))
        ema_state = None if ema_loss is None else ("LOSS" if ema_loss else "HEALTHY")
        reclaim = bool(ema_prev is True and ema_loss is False)
        reloss = bool(ema_prev is False and ema_loss is True)
        if reclaim:
            saw_reclaim = True
        if saw_reclaim and reloss:
            saw_reloss = True
        if price_loss is True:
            if price_ok_after_lost:
                price_reloss_after_reclaim = True
            price_lost_seen = True
            price_ok_after_lost = False
        elif price_loss is False:
            if price_lost_seen:
                price_reclaim_after_lost = True
                price_ok_after_lost = True
        close_ok = tf3.get("close") is not None and _finite(tf3["close"][i])
        ema9_ok = tf3.get("ema9") is not None and _finite(tf3["ema9"][i])
        if close_ok and ema9_ok and float(tf3["close"][i]) > float(tf3["ema9"][i]):
            reclaim_attempt = True
            if vol_det is True:
                vol_det_on_attempt = True
        ft = float(fin[i]) if _finite(fin[i]) else None
        bid, ask = _last_quote(board, t=float(ft)) if ft is not None else (None, None)
        events.append(
            {
                "event_sequence_index": int(idx),
                "event_time": ft,
                "completed_bar_time": _bar_minute(tf3, i),
                "EMA9": float(tf3["ema9"][i]) if ema9_ok else None,
                "EMA21": float(tf3["ema21"][i]) if tf3.get("ema21") is not None and _finite(tf3["ema21"][i]) else None,
                "EMA_state": ema_state,
                "reclaim_event": bool(reclaim),
                "reloss_event": bool(reloss),
                "price_structure_loss": price_loss,
                "volume_deterioration": vol_det,
                "Bid": bid,
                "Ask": ask,
            }
        )
        if ema_loss is not None:
            ema_prev = bool(ema_loss)

    if not saw_reclaim:
        family_a = "A_NO_RECLAIM"
    elif saw_reloss:
        family_a = "A_RECLAIM_THEN_RELOSS"
    else:
        family_a = "A_RECLAIM_THEN_MAINTAIN"

    if not price_lost_seen:
        family_b = "B_PRICE_NEVER_LOST"
    elif not price_reclaim_after_lost:
        family_b = "B_PRICE_LOST_NO_RECLAIM"
    elif price_reloss_after_reclaim:
        family_b = "B_PRICE_RECLAIM_THEN_RELOSS"
    else:
        family_b = "B_PRICE_RECLAIM_THEN_MAINTAIN"

    if not reclaim_attempt:
        family_c = "C_NO_RECLAIM_ATTEMPT"
    elif vol_det_on_attempt:
        family_c = "C_RECLAIM_ATTEMPT_WITH_VOL_DET"
    else:
        family_c = "C_RECLAIM_ATTEMPT_WITHOUT_VOL_DET"

    return {
        "damage_onset": True,
        "onset_t": float(fin[onset_i]) if _finite(fin[onset_i]) else None,
        "onset_i": int(onset_i),
        "post_onset_bar_n": len(events),
        "events": events,
        "family_a": family_a,
        "family_b": family_b,
        "family_c": family_c,
        "reclaimed": bool(saw_reclaim),
        "reloss_after_reclaim": bool(saw_reloss),
        "k6_reached": bool(k6.get("k6_reached")),
        "k6_recovered_before_trigger_n": int(k6.get("k6_recovered_before_trigger_n") or 0),
        "loss_episode_n": int(k6.get("loss_episode_n") or 0),
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
        "K6_SELECTION_N": 0,
        "BAR_COUNT_SEARCH_N": 0,
        "COMBINATION_SEARCH_N": 0,
        "PNL_SELECTION_N": 0,
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


def replay_sequence_day(payload: dict[str, Any]) -> dict[str, Any]:
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
                print(f"{day} v29 sequence stream kept={events_n} last_et={last_et}", flush=True)
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
            rec["terminal_seq"] = None
            if rec.get("actual_filled") and _finite(rec.get("fill_t")):
                rec["terminal_seq"] = eval_post_damage(
                    (tf_by_sym.get(s) or {}).get("tf3") or {},
                    views[s],
                    fill_t=float(rec["fill_t"]),
                )
            rows.append(rec)
        leak["QUEUE_ASSUMPTION_N"] = int(leak.get("QUEUE_FILL_N") or 0)
        leak["OPTIMISTIC_TOUCH_N"] = int(leak.get("OPTIMISTIC_FILL_N") or 0) + int(leak.get("TOUCH_FILL_N") or 0)
        print(
            f"{day} v29 sequence kept_events={events_n} signals={len(rows)} "
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


def save_v29_day_cache(path: Path, body: dict[str, Any]) -> None:
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
                "terminal_seq": r.get("terminal_seq"),
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


def load_v29_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    return body if body else {}


def _smoke() -> None:
    fin = np.asarray([10.0, 20.0, 30.0, 40.0, 50.0], dtype=float)
    ema21 = np.asarray([1.0, 1.0, 1.0, 1.0, 1.0], dtype=float)
    close = np.asarray([0.5, 0.5, 3.0, 3.0, 3.0], dtype=float)
    vol = np.asarray([2.0, 2.0, 3.0, 3.0, 3.0], dtype=float)
    minute = np.asarray([0.0, 180.0, 360.0, 540.0, 720.0], dtype=float)
    board = {"t": np.asarray([10.0, 20.0, 30.0, 40.0, 50.0]), "bid": np.ones(5), "ask": np.ones(5) + 1}
    base = {
        "finalize_t": fin,
        "ema21": ema21,
        "close": close,
        "volume": vol,
        "minute_epoch": minute,
        "bb_mid": np.asarray([1.0, 1.0, 1.0, 1.0, 1.0], dtype=float),
        "rci9": np.asarray([0.0, 0.0, 0.0, 0.0, 0.0], dtype=float),
    }
    p = eval_post_damage({**base, "ema9": np.asarray([0.0, 0.0, 2.0, 2.0, 2.0], dtype=float)}, board, fill_t=5.0)
    assert p["damage_onset"] is True
    assert p["family_a"] == "A_RECLAIM_THEN_MAINTAIN"
    p2 = eval_post_damage({**base, "ema9": np.asarray([0.0, 2.0, 0.0, 0.0, 0.0], dtype=float)}, board, fill_t=5.0)
    assert p2["family_a"] == "A_RECLAIM_THEN_RELOSS"
    p3 = eval_post_damage({**base, "ema9": np.asarray([0.0, 0.0, 0.0, 0.0, 0.0], dtype=float)}, board, fill_t=5.0)
    assert p3["family_a"] == "A_NO_RECLAIM"


_smoke()
assert int(PERSISTENCE_K) == 6

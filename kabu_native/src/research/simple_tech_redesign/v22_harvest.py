"""V22 sealed Capture restream: funnel reasons, t0-anchored paths, 1m/3m/5m states. No C14. No EXIT. No virtual C fills."""
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
from research.simple_tech_entry_family.harvest import _Buf, _board_row, _snap_at, load_day_cache
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.simple_tech_entry_family.v3_harvest import _bid_ok, _last_bid_before
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_entry_family.v7_spec import TF_WIDTH_SEC
from research.simple_tech_entry_family.v11_harvest import _ask_ok, _last_dual_before, quote_row
from research.simple_tech_entry_family.v13_harvest import simulate_e4_only
from research.simple_tech_redesign.isolation import RESEARCH_CACHE
from research.simple_tech_redesign.v22_spec import (
    DEVELOPMENT_CHALLENGER,
    E4_WAIT_BUDGET_SEC,
    PATH_HORIZONS_SEC,
    UNEVAL_CLASSES,
    V12_SPEC_SHA256_EXPECTED,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V22_CACHE = RESEARCH_CACHE / "v22_entry_coverage_rca"
ITAYOSE_STATES = ("ITAYOSE", "PREOPEN", "NOT_OPENED")
VOLUME_MEDIAN_BARS = 5


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


def _pctb(close: Any, lo: Any, up: Any) -> Optional[float]:
    if not _finite(close) or not _finite(lo) or not _finite(up):
        return None
    w = float(up) - float(lo)
    if abs(w) <= 1e-12:
        return None
    return (float(close) - float(lo)) / w


def classify_uneval(snap: dict[str, Any], reason: str) -> str:
    st = str(snap.get("state") or "")
    itayose = any(tok in st for tok in ITAYOSE_STATES)
    r = str(reason or "")
    if r in ("NO_BOARD", "NO_ASK"):
        return "missing_quote"
    if r == "ASK_QTY":
        return "missing_qty"
    if r == "SPECIAL":
        return "special_quote"
    if r == "STALE":
        return "stale"
    if itayose and r in ("NOT_CONTINUOUS", "NOT_EXECUTABLE", ""):
        return "itayose"
    return "other"


def _path_excursions(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    ask0: float,
    mid0: Optional[float],
    path_end: float,
) -> dict[str, Optional[float]]:
    out = {
        "mfe_ask_bid": None,
        "mae_ask_bid": None,
        "mfe_mid_mid": None,
        "mae_mid_mid": None,
        "mfe_ask_mid": None,
        "mae_ask_mid": None,
    }
    t = board.get("t")
    if t is None or int(t.size) == 0 or not _finite(ask0) or float(ask0) <= 0:
        return out
    i0 = int(np.searchsorted(t, float(t0), side="right"))
    mfe_ab = mae_ab = mfe_mm = mae_mm = mfe_am = mae_am = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(t0) + 1e-12:
            continue
        if ti > float(path_end) + 1e-12:
            break
        if _bid_ok(board, i):
            bps = (float(board["bid"][i]) / float(ask0) - 1.0) * 10000.0
            mfe_ab = bps if mfe_ab is None or bps > mfe_ab else mfe_ab
            mae_ab = bps if mae_ab is None or bps < mae_ab else mae_ab
        if _bid_ok(board, i) and _ask_ok(board, i):
            mid = (float(board["bid"][i]) + float(board["ask"][i])) / 2.0
            if mid > 0 and mid0 is not None and float(mid0) > 0:
                mm = (mid / float(mid0) - 1.0) * 10000.0
                mfe_mm = mm if mfe_mm is None or mm > mfe_mm else mfe_mm
                mae_mm = mm if mae_mm is None or mm < mae_mm else mae_mm
            am = (mid / float(ask0) - 1.0) * 10000.0
            mfe_am = am if mfe_am is None or am > mfe_am else mfe_am
            mae_am = am if mae_am is None or am < mae_am else mae_am
    out["mfe_ask_bid"] = mfe_ab
    out["mae_ask_bid"] = mae_ab
    out["mfe_mid_mid"] = mfe_mm
    out["mae_mid_mid"] = mae_mm
    out["mfe_ask_mid"] = mfe_am
    out["mae_ask_mid"] = mae_am
    return out


def _last_i(ind: dict[str, np.ndarray], t_cut: float) -> Optional[int]:
    fin = ind.get("finalize_t")
    if fin is None or int(fin.size) == 0:
        return None
    i = int(np.searchsorted(fin, float(t_cut), side="right") - 1)
    if i < 0:
        return None
    return i


def bar_state(ind: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    close = float(ind["close"][i])
    ema9 = float(ind["ema9"][i])
    ema21 = float(ind["ema21"][i])
    vol = float(ind["volume"][i])
    vol_med = None
    if i >= int(VOLUME_MEDIAN_BARS):
        base = ind["volume"][i - int(VOLUME_MEDIAN_BARS) : i]
        if int(base.size) == int(VOLUME_MEDIAN_BARS) and bool(np.all(np.isfinite(base))) and float(np.median(base)) > 0:
            vol_med = float(np.median(base))
    return {
        "close": close if close == close else None,
        "ema9": ema9 if ema9 == ema9 else None,
        "ema21": ema21 if ema21 == ema21 else None,
        "bb_upper": float(ind["bb_upper"][i]) if _finite(ind["bb_upper"][i]) else None,
        "bb_lower": float(ind["bb_lower"][i]) if _finite(ind["bb_lower"][i]) else None,
        "rci9": float(ind["rci9"][i]) if _finite(ind["rci9"][i]) else None,
        "volume": vol if vol == vol else None,
        "vol_accel": (vol / vol_med) if vol_med is not None and vol == vol else None,
        "pctb": _pctb(close, ind["bb_lower"][i], ind["bb_upper"][i]),
        "ema_gap_bps": _bps(ema9, ema21),
        "close_ema9_bps": _bps(close, ema9),
        "trend": bool(trend_up(ind, i)),
        "pullback": bool(pullback_setup(ind, i)),
        "rci_cross": bool(reversal_rci(ind, i)),
        "volume_ok": bool(volume_confirm(ind, i)),
        "finalize_t": float(ind["finalize_t"][i]) if _finite(ind["finalize_t"][i]) else None,
        "bar_start": float(ind["minute_epoch"][i]) if _finite(ind["minute_epoch"][i]) else None,
    }


def _tf_pack(by_tf: dict[str, dict[str, np.ndarray]], t_cut: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tf, ind in by_tf.items():
        i = _last_i(ind, t_cut)
        out[tf] = bar_state(ind, i) if i is not None else None
    return out


def _empty_path(rec: dict[str, Any]) -> None:
    for h in PATH_HORIZONS_SEC:
        hid = int(h)
        rec[f"mid_mid_{hid}"] = None
        rec[f"ask_mid_{hid}"] = None
        rec[f"ask_bid_{hid}"] = None
        rec[f"mfe_ask_bid_{hid}"] = None
        rec[f"mae_ask_bid_{hid}"] = None
        rec[f"mfe_mid_mid_{hid}"] = None
        rec[f"mae_mid_mid_{hid}"] = None
        rec[f"mfe_ask_mid_{hid}"] = None
        rec[f"mae_ask_mid_{hid}"] = None
        rec[f"path_clamped_{hid}"] = False


def attach_signal_paths(rec: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float) -> dict[str, Any]:
    if not rec.get("executable_signal") or not _finite(rec.get("ask0")):
        _empty_path(rec)
        return rec
    t0 = float(rec["t0"])
    ask0 = float(rec["ask0"])
    mid0 = float(rec["mid0"]) if _finite(rec.get("mid0")) else None
    for h in PATH_HORIZONS_SEC:
        hid = int(h)
        mark_t = min(float(t0) + float(h), float(am_end))
        rec[f"path_clamped_{hid}"] = bool(float(t0) + float(h) > float(am_end) + 1e-12)
        bid_exec, _bt = _last_bid_before(board, t0=t0, mark_t=mark_t)
        dual = _last_dual_before(board, t0=t0, mark_t=mark_t)
        rec[f"ask_bid_{hid}"] = _bps(bid_exec, ask0)
        rec[f"mid_mid_{hid}"] = _bps(dual.get("mid"), mid0) if mid0 is not None else None
        rec[f"ask_mid_{hid}"] = _bps(dual.get("mid"), ask0)
        if hid in (60, 180, 300) and rec.get(f"full_{hid}") is not None:
            rec[f"ask_bid_{hid}"] = rec.get(f"full_{hid}")
            rec[f"mid_mid_{hid}"] = rec.get(f"gross_{hid}")
            rec[f"ask_mid_{hid}"] = rec.get(f"cross_{hid}")
        path = _path_excursions(board, t0=t0, ask0=ask0, mid0=mid0, path_end=mark_t)
        rec[f"mfe_ask_bid_{hid}"] = path["mfe_ask_bid"]
        rec[f"mae_ask_bid_{hid}"] = path["mae_ask_bid"]
        rec[f"mfe_mid_mid_{hid}"] = path["mfe_mid_mid"]
        rec[f"mae_mid_mid_{hid}"] = path["mae_mid_mid"]
        rec[f"mfe_ask_mid_{hid}"] = path["mfe_ask_mid"]
        rec[f"mae_ask_mid_{hid}"] = path["mae_ask_mid"]
    return rec


def _slim_e4(rec: dict[str, Any]) -> dict[str, Any]:
    arm = dict((rec.get("exec") or {}).get(DEVELOPMENT_CHALLENGER) or {})
    return {
        "filled": bool(arm.get("filled")),
        "fill_price": arm.get("fill_price") if arm.get("filled") else None,
        "fill_t": arm.get("fill_t") if arm.get("filled") else None,
        "limit_price": arm.get("limit_price"),
        "wait_budget_sec": float(E4_WAIT_BUDGET_SEC),
        "waited_sec": arm.get("waited_sec") if arm.get("filled") else None,
        "collapsed_to_bid": bool(arm.get("collapsed_to_bid") or rec.get("inside_collapsed_to_bid")),
        "evidence": arm.get("evidence") if arm.get("filled") else None,
        "fill_reason": None if arm.get("filled") else arm.get("fill_reason"),
        "nonfill_class": None if arm.get("filled") else arm.get("nonfill_class"),
    }


def coverage_row(
    sig: dict[str, Any],
    board: dict[str, np.ndarray],
    by_tf: dict[str, dict[str, np.ndarray]],
    *,
    am_end: float,
    leak: dict[str, Any],
) -> dict[str, Any]:
    rec = quote_row(sig, board, am_end=am_end, leak=leak)
    snap = _snap_at(board, float(rec["t0"]))
    rec["board_state"] = str(snap.get("state") or "") if snap.get("ok") else ""
    rec["board_ok"] = bool(sig.get("board_ok"))
    rec["board_reason"] = str(sig.get("board_reason") or "")
    rec["trend"] = bool(sig.get("trend"))
    rec["pullback"] = bool(sig.get("pullback"))
    rec["rci"] = bool(sig.get("rci"))
    rec["pa"] = bool(sig.get("pa"))
    rec["volume"] = bool(sig.get("volume"))
    rec["PQ1"] = sig.get("PQ1")
    rec["PQ3"] = sig.get("PQ3")
    rec["TQ1"] = sig.get("TQ1")
    rec["TQ2"] = sig.get("TQ2")
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
        attach_signal_paths(rec, board, am_end=am_end)
    else:
        rec["cohort"] = "C"
        rec["e4_filled"] = False
        _empty_path(rec)
    t0 = float(rec["t0"])
    rec["state_t0"] = _tf_pack(by_tf, t0)
    rec["state_path"] = {}
    if rec.get("executable_signal"):
        for h in PATH_HORIZONS_SEC:
            rec["state_path"][str(int(h))] = _tf_pack(by_tf, min(t0 + float(h), float(am_end)))
    return rec


def replay_coverage_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "VIRTUAL_FILL_C_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "FUTURE_BOARD_N": 0,
        "FUTURE_BAR_N": 0,
        "NO_ASK0_N": 0,
        "MID0_MISS_N": 0,
        "FULL_MISS_N": 0,
        "GROSS_MISS_N": 0,
        "DUAL_BID_NE_EXEC_BID_N": 0,
        "NOT_ELIGIBLE_N": 0,
        "INSIDE_COLLAPSE_N": 0,
        "ASK_CROSS_LIMIT_N": 0,
        "ASK_CROSS_FILL_N": 0,
        "INSIDE_ASK_CROSS_FILL_N": 0,
        "PARTIAL_BUCKET_N": 0,
        "GAP_BUCKET_N": 0,
        "LATE_FINALIZE_N": 0,
        "SESSION_TAIL_SKIP_N": 0,
        "FIXED180_PNL_N": 0,
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
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in needed}
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
            builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            if not row["executable"]:
                st = str(row.get("state") or "")
                if any(tok in st for tok in ITAYOSE_STATES):
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 200000 == 0:
                print(f"{day} v22 coverage stream kept={events_n} last_et={last_et}", flush=True)
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
        rows = []
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rows.append(
                coverage_row(
                    sig,
                    views[s],
                    tf_by_sym.get(s) or {},
                    am_end=am_end,
                    leak=leak,
                )
            )
        print(
            f"{day} v22 coverage kept_events={events_n} signals={len(rows)} "
            f"integ_fail={integ_fail} last_et={last_et}",
            flush=True,
        )
        del bufs, views, builders, tf_by_sym
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
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def save_v22_day_cache(path: Path, body: dict[str, Any]) -> None:
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
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def load_v22_day_cache(path: Path, spec_sha: str) -> dict[str, Any]:
    body = load_day_cache(path, spec_sha)
    if not body:
        return {}
    if str(body.get("v12_spec_sha") or "") != V12_SPEC_SHA256_EXPECTED:
        return {}
    return body


# silence unused import of TF_WIDTH_SEC if linters flag it; kept as architecture lock
assert abs(float(TF_WIDTH_SEC["TF3"]) - 180.0) < 1e-12
assert abs(float(TF_WIDTH_SEC["TF5"]) - 300.0) < 1e-12

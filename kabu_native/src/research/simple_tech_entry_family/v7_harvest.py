"""V7: causal 1m/3m/5m bars + nested role flags + Ask markout 60/180/300. No C14. No EXIT. No mixed TF."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row, _snap_at
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    evaluable,
    execution_eligible,
    price_action,
    pullback_setup,
    reversal_rci,
    trend_up,
    volume_confirm,
)
from research.simple_tech_entry_family.v3_harvest import _last_bid_before, _path_stats, ask_entry_ok
from research.simple_tech_entry_family.v3_spec import PATH_SEC
from research.simple_tech_entry_family.v4_harvest import volume_persistence_300s
from research.simple_tech_entry_family.v7_bars import agg_integrity, aggregate_bars
from research.simple_tech_entry_family.v7_spec import MARKOUT_HORIZONS_SEC, TF_IDS, TF_WIDTH_SEC
from small_paper.v1r_live_dual_lane import session_end_for_position

V7_CACHE = CACHE / "v7_timeframe_role_rca"
SLIM_KEYS = (
    "tf",
    "date",
    "symbol",
    "t0",
    "bar_start",
    "width_sec",
    "trend",
    "pullback",
    "rci",
    "pa",
    "volume",
    "s1",
    "s2",
    "s3",
    "s4",
    "s5",
    "s6",
    "s7",
    "executable_signal",
    "board_ok",
    "board_reason",
    "markout_60",
    "markout_180",
    "markout_300",
    "mfe_bps",
    "mae_bps",
    "vol_accel",
    "VQ2",
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _empty_funnel() -> dict[str, int]:
    return {k: 0 for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7")}


def markout_slim(board: dict[str, np.ndarray], *, t0: float, am_end: float) -> dict[str, Any]:
    snap = _snap_at(board, t0)
    ok, reason = ask_entry_ok(snap)
    ask = float(snap["ask"]) if snap.get("ok") and _finite(snap.get("ask")) else None
    rec: dict[str, Any] = {
        "executable_signal": bool(ok),
        "ask_reason": reason,
        "ask_t0": ask if ok else None,
        "mfe_bps": None,
        "mae_bps": None,
        "markout_60": None,
        "markout_180": None,
        "markout_300": None,
    }
    if not ok or ask is None or ask <= 0:
        return rec
    path_end = min(float(t0) + float(PATH_SEC), float(am_end))
    path = _path_stats(board, t0=t0, ask=float(ask), path_end=path_end)
    rec["mfe_bps"] = path["mfe_bps"]
    rec["mae_bps"] = path["mae_bps"]
    for h in MARKOUT_HORIZONS_SEC:
        mark_t = min(float(t0) + float(h), float(am_end))
        bid_h, _bid_t = _last_bid_before(board, t0=t0, mark_t=mark_t)
        if bid_h is None or bid_h <= 0:
            rec[f"markout_{int(h)}"] = None
        else:
            rec[f"markout_{int(h)}"] = (float(bid_h) / float(ask) - 1.0) * 10000.0
    return rec


def _emit_tf(
    *,
    day: str,
    symbol: str,
    tf: str,
    width: float,
    ind: dict[str, np.ndarray],
    board: dict[str, np.ndarray],
    am_end: float,
    leak: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    n = int(ind["close"].size)
    rows: list[dict[str, Any]] = []
    funnel = _empty_funnel()
    vol_t = board.get("t")
    vol_c = board.get("cum_vol")
    for i in range(n):
        if not evaluable(i, n):
            continue
        t0 = float(ind["finalize_t"][i])
        if not (t0 == t0):
            continue
        if t0 + float(DEV_WAIT_SEC) > float(am_end) + 1e-12:
            continue
        snap = _snap_at(board, t0)
        if snap.get("ok") and _finite(snap.get("t")) and float(snap["t"]) > float(t0) + 1e-12:
            leak["FUTURE_BOARD_N"] = int(leak.get("FUTURE_BOARD_N") or 0) + 1
        trend = bool(trend_up(ind, i))
        pb = bool(pullback_setup(ind, i))
        rci = bool(reversal_rci(ind, i))
        pa = bool(price_action(ind, i))
        vol = bool(volume_confirm(ind, i))
        s1 = trend
        s2 = bool(s1) and pb
        s3 = bool(s2) and rci
        s4 = bool(s3) and pa
        s5 = bool(s4) and vol
        b_ok, bmeta = board_support(snap) if snap.get("ok") else (False, {"board_ok": False, "board_reason": "NO_BOARD"})
        s6 = bool(s5) and bool(b_ok)
        s7 = bool(s6) and bool(snap.get("ok")) and bool(execution_eligible(snap))
        mk = markout_slim(board, t0=t0, am_end=am_end)
        vol_med5 = float(np.median(ind["volume"][i - 5 : i])) if i >= 5 else None
        vol_accel = None
        if vol_med5 is not None and vol_med5 > 0:
            vol_accel = float(ind["volume"][i]) / float(vol_med5)
        vq2 = None
        if vol_t is not None and vol_c is not None and int(vol_t.size) > 0:
            vq2 = volume_persistence_300s(vol_t, vol_c, t0)
        rec = {
            "tf": tf,
            "date": day,
            "symbol": symbol,
            "t0": t0,
            "bar_start": float(ind["minute_epoch"][i]),
            "width_sec": float(width),
            "trend": trend,
            "pullback": pb,
            "rci": rci,
            "pa": pa,
            "volume": vol,
            "s1": s1,
            "s2": s2,
            "s3": s3,
            "s4": s4,
            "s5": s5,
            "s6": s6,
            "s7": s7,
            "executable_signal": bool(mk.get("executable_signal")),
            "board_ok": bool(b_ok),
            "board_reason": bmeta.get("board_reason"),
            "markout_60": mk.get("markout_60"),
            "markout_180": mk.get("markout_180"),
            "markout_300": mk.get("markout_300"),
            "mfe_bps": mk.get("mfe_bps"),
            "mae_bps": mk.get("mae_bps"),
            "vol_accel": vol_accel,
            "VQ2": vq2,
        }
        rows.append(rec)
        funnel["s0"] += 1
        if s1:
            funnel["s1"] += 1
        if s2:
            funnel["s2"] += 1
        if s3:
            funnel["s3"] += 1
        if s4:
            funnel["s4"] += 1
        if s5:
            funnel["s5"] += 1
        if s6:
            funnel["s6"] += 1
        if s7:
            funnel["s7"] += 1
    return rows, funnel


def process_v7_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak: dict[str, Any] = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_BOARD_N": 0,
        "FUTURE_BAR_N": 0,
        "C14_REPLAY_N": 0,
        "EXIT_SIM_N": 0,
        "MIXED_TF_STRATEGY_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "EMA_CHANGE_N": 0,
        "BB_CHANGE_N": 0,
        "RCI_CHANGE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "PERSISTENCE_AND_N": 0,
        "PARTIAL_BUCKET_N": 0,
        "GAP_BUCKET_N": 0,
        "LATE_FINALIZE_N": 0,
        "SESSION_TAIL_SKIP_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        bufs: dict[str, _Buf] = {s: _Buf() for s in universe}
        cums: dict[str, list[float]] = {s: [] for s in universe}
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        events_n = 0
        last_et: Optional[float] = None
        last_seq: Optional[int] = None
        for rec in iter_push(capture):
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                leak["UNIVERSE_SKIP_N"] += 1
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                leak["NO_EVENT_TIME_N"] += 1
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            recv = record_event_stamp(rec)
            seq = int(rec.get("sequence") or 0)
            if seq > 0:
                last_seq = seq
            if recv:
                pay["received_at"] = recv
            last_et = float(et)
            events_n += 1
            row = _board_row(pay, float(et))
            bufs[sym].append(row)
            cv = row.get("cum_vol")
            cums[sym].append(float(cv) if _finite(cv) else float("nan"))
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
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 400000 == 0:
                print(f"{day} v7 stream events={events_n} last_et={last_et}", flush=True)

        rows: list[dict[str, Any]] = []
        bar_rows: list[dict[str, Any]] = []
        funnels = {tf: _empty_funnel() for tf in TF_IDS}
        integ_fail = 0
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ1 = bar_integrity(raw, am_start=am_start, am_end=am_end)
            if not integ1.get("ok"):
                integ_fail += 1
            leak["FUTURE_BAR_N"] = int(leak.get("FUTURE_BAR_N") or 0) + int(integ1.get("FUTURE_BAR_N") or 0)
            board = bufs[s].view()
            board["cum_vol"] = np.asarray(cums[s], dtype=float)
            by_tf: dict[str, dict[str, np.ndarray]] = {"TF1": raw}
            for tf in ("TF3", "TF5"):
                arr, alk = aggregate_bars(raw, width_sec=float(TF_WIDTH_SEC[tf]), am_start=am_start, am_end=am_end)
                for k in ("PARTIAL_BUCKET_N", "GAP_BUCKET_N", "LATE_FINALIZE_N", "SESSION_TAIL_SKIP_N"):
                    leak[k] = int(leak.get(k) or 0) + int(alk.get(k) or 0)
                integ = agg_integrity(arr, width_sec=float(TF_WIDTH_SEC[tf]), am_start=am_start, am_end=am_end)
                leak["FUTURE_BAR_N"] = int(leak.get("FUTURE_BAR_N") or 0) + int(integ.get("FUTURE_BAR_N") or 0)
                if not integ.get("ok"):
                    integ_fail += 1
                by_tf[tf] = arr
                bar_rows.append({"date": day, "symbol": s, "tf": tf, **integ, **{k: alk.get(k) for k in alk}})
            bar_rows.append({"date": day, "symbol": s, "tf": "TF1", **integ1, **builders[s].leak})
            for tf in TF_IDS:
                arr = by_tf[tf]
                if int(arr["minute_epoch"].size) == 0:
                    continue
                ind = attach_indicators(arr)
                got, fn = _emit_tf(
                    day=day,
                    symbol=s,
                    tf=tf,
                    width=float(TF_WIDTH_SEC[tf]),
                    ind=ind,
                    board=board,
                    am_end=am_end,
                    leak=leak,
                )
                rows.extend(got)
                for k, v in fn.items():
                    funnels[tf][k] += int(v)
            del board
        print(
            f"{day} v7 events={events_n} rows={len(rows)} "
            f"tf1_s0={funnels['TF1']['s0']} tf3_s0={funnels['TF3']['s0']} tf5_s0={funnels['TF5']['s0']} "
            f"bars_fail={integ_fail} last_et={last_et}",
            flush=True,
        )
        del bufs, builders, cums
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
            "last_seq": last_seq,
            "rows": rows,
            "bar_rows": bar_rows,
            "funnels": funnels,
            "leak": leak,
            "integ_fail_n": integ_fail,
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


def save_v7_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim_rows = []
    for r in list(body.get("rows") or []):
        slim_rows.append({k: r.get(k) for k in SLIM_KEYS})
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "last_seq": body.get("last_seq"),
            "leak": body.get("leak"),
            "integ_fail_n": body.get("integ_fail_n"),
            "elapsed_sec": body.get("elapsed_sec"),
            "funnels": body.get("funnels"),
            "bar_rows": body.get("bar_rows"),
            "rows": slim_rows,
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")

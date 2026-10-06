"""V3: V1 signals + Ask_t0 + executable Bid markouts. No C14. No EXIT. Parallelism=1."""
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

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family.harvest import _Buf, _board_row, _snap_at
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC
from small_paper.v1r_live_dual_lane import session_end_for_position

CACHE = NATIVE / "results" / "research" / "simple_tech_entry_family" / "_work"
V3_CACHE = CACHE / "v3_exit_neutral"
CONTINUOUS_STATES = {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"}


def _continuous(snap_or_state: Any, executable: bool | None = None) -> bool:
    if isinstance(snap_or_state, dict):
        st = str(snap_or_state.get("state") or "")
        exe = bool(snap_or_state.get("executable"))
        return exe and st in CONTINUOUS_STATES
    return bool(executable) and str(snap_or_state or "") in CONTINUOUS_STATES


def ask_entry_ok(snap: dict[str, Any]) -> tuple[bool, str]:
    if not snap.get("ok"):
        return False, "NO_BOARD"
    if not bool(snap.get("executable")):
        return False, "NOT_EXECUTABLE"
    if bool(snap.get("special")):
        return False, "SPECIAL"
    if not _continuous(snap):
        return False, "NOT_CONTINUOUS"
    fresh = snap.get("fresh_sec")
    try:
        fv = float(fresh)
    except (TypeError, ValueError):
        return False, "STALE"
    if not (fv == fv) or fv > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return False, "STALE"
    ask = snap.get("ask")
    aq = snap.get("ask_qty")
    try:
        av = float(ask)
        qv = float(aq)
    except (TypeError, ValueError):
        return False, "NO_ASK"
    if not (av == av) or av <= 0:
        return False, "NO_ASK"
    if not (qv == qv) or qv < float(MIN_QTY):
        return False, "ASK_QTY"
    return True, ""


def _bid_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]):
        return False
    if bool(board["special"][i]):
        return False
    st = str(board["board_execution_state"][i] or "")
    if st not in CONTINUOUS_STATES:
        return False
    fresh = float(board["fresh_sec"][i])
    if not (fresh == fresh) or fresh > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return False
    bid = float(board["bid"][i])
    bq = float(board["bid_qty"][i])
    if not (bid == bid) or bid <= 0:
        return False
    if not (bq == bq) or bq < float(MIN_QTY):
        return False
    return True


def _last_bid_before(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    mark_t: float,
) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0:
        return None, None
    i_hi = int(np.searchsorted(t, float(mark_t), side="right") - 1)
    i_lo = int(np.searchsorted(t, float(t0), side="right"))
    for i in range(i_hi, i_lo - 1, -1):
        if i < 0:
            break
        ti = float(t[i])
        if ti > float(mark_t) + 1e-12:
            continue
        if ti <= float(t0) + 1e-12:
            break
        if _bid_ok(board, i):
            return float(board["bid"][i]), ti
    return None, None


def _path_stats(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    ask: float,
    path_end: float,
) -> dict[str, Any]:
    t = board.get("t")
    out: dict[str, Any] = {
        "mfe_bps": None,
        "mae_bps": None,
        "cost_recovered_t": None,
    }
    if t is None or int(t.size) == 0 or not (ask == ask) or ask <= 0:
        return out
    i0 = int(np.searchsorted(t, float(t0), side="right"))
    mfe = None
    mae = None
    rec_t = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(t0) + 1e-12:
            continue
        if ti > float(path_end) + 1e-12:
            break
        if not _bid_ok(board, i):
            continue
        bid = float(board["bid"][i])
        bps = (bid / float(ask) - 1.0) * 10000.0
        if mfe is None or bps > mfe:
            mfe = bps
        if mae is None or bps < mae:
            mae = bps
        if rec_t is None and bid + 1e-12 >= float(ask):
            rec_t = ti
    out["mfe_bps"] = mfe
    out["mae_bps"] = mae
    out["cost_recovered_t"] = rec_t
    return out


def markout_row(sig: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float) -> dict[str, Any]:
    t0 = float(sig["t0"])
    snap = _snap_at(board, t0)
    ok, reason = ask_entry_ok(snap)
    ask = float(snap["ask"]) if snap.get("ok") and snap.get("ask") == snap.get("ask") else None
    rec = {
        "date": sig.get("date"),
        "session": "AM",
        "symbol": str(sig.get("symbol") or "").replace(".T", ""),
        "t0": t0,
        "bar_minute": sig.get("bar_minute"),
        "passive_filled": bool(sig.get("WOULD_FILL")),
        "ask_t0": ask if ok else None,
        "ask_qty": snap.get("ask_qty") if snap.get("ok") else None,
        "bid_t0": snap.get("bid") if snap.get("ok") else None,
        "executable_signal": bool(ok),
        "ask_reason": reason,
        "volume": sig.get("volume"),
        "vol_accel": sig.get("vol_accel"),
        "rci9": sig.get("rci9"),
        "ema9": sig.get("ema9"),
        "ema21": sig.get("ema21"),
        "close": sig.get("close"),
        "bb_upper": sig.get("bb_upper"),
        "fwd_3m": sig.get("fwd_3m"),
        "mfe_5m": sig.get("mfe_5m"),
        "up_first": sig.get("up_first"),
        "cost_exceed": sig.get("cost_exceed"),
        "c14_used": False,
    }
    if not ok or ask is None or ask <= 0:
        for h in HORIZONS_SEC:
            rec[f"markout_{int(h)}"] = None
            rec[f"cost_recovered_{int(h)}"] = False
        rec["mfe_bps"] = None
        rec["mae_bps"] = None
        return rec
    path_end = min(float(t0) + float(PATH_SEC), float(am_end))
    path = _path_stats(board, t0=t0, ask=float(ask), path_end=path_end)
    rec["mfe_bps"] = path["mfe_bps"]
    rec["mae_bps"] = path["mae_bps"]
    rec_t = path["cost_recovered_t"]
    rec["cost_recovered_t"] = rec_t
    for h in HORIZONS_SEC:
        mark_t = min(float(t0) + float(h), float(am_end))
        bid_h, bid_t = _last_bid_before(board, t0=t0, mark_t=mark_t)
        if bid_h is None or bid_h <= 0:
            rec[f"markout_{int(h)}"] = None
            rec[f"mark_bid_{int(h)}"] = None
            rec[f"mark_t_{int(h)}"] = None
        else:
            rec[f"markout_{int(h)}"] = (float(bid_h) / float(ask) - 1.0) * 10000.0
            rec[f"mark_bid_{int(h)}"] = float(bid_h)
            rec[f"mark_t_{int(h)}"] = bid_t
        rec[f"cost_recovered_{int(h)}"] = bool(rec_t is not None and float(rec_t) <= float(mark_t) + 1e-12)
    return rec


def process_v3_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    signals = list(payload.get("signals") or [])
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "C14_REPLAY_N": 0,
        "EXIT_SIM_N": 0,
        "FUTURE_ASK_USE_N": 0,
        "MIDPOINT_ENTRY_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_LABEL_AS_FEATURE_N": 0,
    }
    if not signals:
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
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
            if not row["executable"]:
                st = str(row.get("state") or "")
                if "ITAYOSE" in st or "PREOPEN" in st or "NOT_OPENED" in st:
                    leak["ITAYOSE_SKIP_N"] += 1
                elif "SPECIAL" in st:
                    leak["SPECIAL_SKIP_N"] += 1
                else:
                    leak["INVALID_SKIP_N"] += 1
            if events_n % 200000 == 0:
                print(f"{day} v3 stream kept={events_n} last_et={last_et}", flush=True)

        rows = []
        views = {s: bufs[s].view() for s in needed}
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rows.append(markout_row(sig, views[s], am_end=am_end))
        print(f"{day} v3 kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
        del bufs, views
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
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


def save_v3_day_cache(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = json_sanitize(
        {
            "ok": body.get("ok"),
            "date": body.get("date"),
            "spec_sha": body.get("spec_sha"),
            "events_n": body.get("events_n"),
            "last_et": body.get("last_et"),
            "leak": body.get("leak"),
            "elapsed_sec": body.get("elapsed_sec"),
            "rows": body.get("rows"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")

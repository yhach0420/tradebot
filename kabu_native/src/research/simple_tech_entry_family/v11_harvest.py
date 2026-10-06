"""V11 quote replay on frozen B1 signals. Bid/Ask/Mid at t0 and H. No signal recapture. No EXIT."""
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
from research.simple_tech_entry_family.harvest import CACHE, _Buf, _board_row, _snap_at
from research.simple_tech_entry_family.v3_harvest import _bid_ok, _last_bid_before, ask_entry_ok
from research.simple_tech_entry_family.v10_harvest import arm_pass
from research.simple_tech_entry_family.v11_spec import MARKOUT_HORIZONS_SEC
from small_paper.v1r_live_dual_lane import session_end_for_position

V11_CACHE = CACHE / "v11_signal_execution_cost_rca"


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


def _ask_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]):
        return False
    if bool(board["special"][i]):
        return False
    st = str(board["board_execution_state"][i] or "")
    if st not in {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"}:
        return False
    fresh = float(board["fresh_sec"][i])
    if not (fresh == fresh) or fresh > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return False
    ask = float(board["ask"][i])
    aq = float(board["ask_qty"][i])
    if not (ask == ask) or ask <= 0:
        return False
    if not (aq == aq) or aq < float(MIN_QTY):
        return False
    return True


def _last_dual_before(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    mark_t: float,
) -> dict[str, Any]:
    t = board.get("t")
    out = {"bid": None, "ask": None, "mid": None, "t": None, "i": None}
    if t is None or int(t.size) == 0:
        return out
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
        if _bid_ok(board, i) and _ask_ok(board, i):
            bid = float(board["bid"][i])
            ask = float(board["ask"][i])
            out["bid"] = bid
            out["ask"] = ask
            out["mid"] = (bid + ask) / 2.0
            out["t"] = ti
            out["i"] = i
            return out
    return out


def is_b1(row: dict[str, Any]) -> bool:
    return bool(arm_pass(row, "B1_RCI"))


def quote_row(sig: dict[str, Any], board: dict[str, np.ndarray], *, am_end: float, leak: dict[str, Any]) -> dict[str, Any]:
    t0 = float(sig["t0"])
    snap = _snap_at(board, t0)
    if snap.get("ok") and _finite(snap.get("t")) and float(snap["t"]) > float(t0) + 1e-12:
        leak["FUTURE_BOARD_N"] = int(leak.get("FUTURE_BOARD_N") or 0) + 1
    ok, reason = ask_entry_ok(snap)
    ask0 = float(snap["ask"]) if snap.get("ok") and _finite(snap.get("ask")) else None
    bid0 = float(snap["bid"]) if snap.get("ok") and _finite(snap.get("bid")) and float(snap["bid"]) > 0 else None
    mid0 = (float(bid0) + float(ask0)) / 2.0 if bid0 is not None and ask0 is not None and float(ask0) > 0 else None
    spread0 = ((float(ask0) - float(bid0)) / float(mid0) * 10000.0) if mid0 is not None and mid0 > 0 else None
    rec: dict[str, Any] = {
        "date": sig.get("date"),
        "symbol": str(sig.get("symbol") or "").replace(".T", ""),
        "t0": t0,
        "trend": bool(sig.get("trend")),
        "pullback": bool(sig.get("pullback")),
        "rci": bool(sig.get("rci")),
        "board_ok": bool(sig.get("board_ok")),
        "executable_signal": bool(ok),
        "ask_reason": reason,
        "bid0": bid0 if ok else None,
        "ask0": ask0 if ok else None,
        "mid0": mid0 if ok else None,
        "spread0_bps": spread0 if ok else None,
        "cached_full_60": sig.get("markout_60"),
        "cached_full_180": sig.get("markout_180"),
        "cached_full_300": sig.get("markout_300"),
    }
    if not ok or ask0 is None or ask0 <= 0:
        leak["NO_ASK0_N"] = int(leak.get("NO_ASK0_N") or 0) + 1
        for h in MARKOUT_HORIZONS_SEC:
            hid = int(h)
            rec[f"bid_{hid}"] = None
            rec[f"ask_{hid}"] = None
            rec[f"mid_{hid}"] = None
            rec[f"full_{hid}"] = None
            rec[f"gross_{hid}"] = None
            rec[f"cross_{hid}"] = None
            rec[f"entry_burden_{hid}"] = None
            rec[f"exit_burden_{hid}"] = None
            rec[f"total_deg_{hid}"] = None
            rec[f"markout_{hid}"] = None
        return rec
    if mid0 is None:
        leak["MID0_MISS_N"] = int(leak.get("MID0_MISS_N") or 0) + 1
    for h in MARKOUT_HORIZONS_SEC:
        hid = int(h)
        mark_t = min(float(t0) + float(h), float(am_end))
        bid_exec, _bt = _last_bid_before(board, t0=t0, mark_t=mark_t)
        dual = _last_dual_before(board, t0=t0, mark_t=mark_t)
        rec[f"bid_{hid}"] = dual.get("bid")
        rec[f"ask_{hid}"] = dual.get("ask")
        rec[f"mid_{hid}"] = dual.get("mid")
        rec[f"bid_exec_{hid}"] = bid_exec
        if bid_exec is not None and dual.get("bid") is not None and abs(float(bid_exec) - float(dual["bid"])) > 1e-8:
            leak["DUAL_BID_NE_EXEC_BID_N"] = int(leak.get("DUAL_BID_NE_EXEC_BID_N") or 0) + 1
        full = _bps(bid_exec, ask0)
        mid_h = dual.get("mid")
        gross = _bps(mid_h, mid0) if mid0 is not None else None
        cross = _bps(mid_h, ask0)
        rec[f"full_{hid}"] = full
        rec[f"gross_{hid}"] = gross
        rec[f"cross_{hid}"] = cross
        rec[f"entry_burden_{hid}"] = (float(cross) - float(gross)) if cross is not None and gross is not None else None
        rec[f"exit_burden_{hid}"] = (float(full) - float(cross)) if full is not None and cross is not None else None
        rec[f"total_deg_{hid}"] = (float(full) - float(gross)) if full is not None and gross is not None else None
        rec[f"markout_{hid}"] = full
        if full is None:
            leak["FULL_MISS_N"] = int(leak.get("FULL_MISS_N") or 0) + 1
        if gross is None:
            leak["GROSS_MISS_N"] = int(leak.get("GROSS_MISS_N") or 0) + 1
    return rec


def process_v11_day(payload: dict[str, Any]) -> dict[str, Any]:
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
        "SIGNAL_RECAPTURE_N": 0,
        "ENTRY_RULE_CHANGE_N": 0,
        "THRESHOLD_SEARCH_N": 0,
        "INVERSE_BOARD_GATE_N": 0,
        "BOARD_HARD_VETO_N": 0,
        "PA_RESTORE_N": 0,
        "VOLUME_RESTORE_N": 0,
        "FUTURE_BOARD_N": 0,
        "FUTURE_ASK_USE_N": 0,
        "MIDPOINT_ENTRY_N": 0,
        "NO_ASK0_N": 0,
        "MID0_MISS_N": 0,
        "FULL_MISS_N": 0,
        "GROSS_MISS_N": 0,
        "DUAL_BID_NE_EXEC_BID_N": 0,
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
                print(f"{day} v11 stream kept={events_n} last_et={last_et}", flush=True)
        rows = []
        views = {s: bufs[s].view() for s in needed}
        for sig in signals:
            s = _bare(sig.get("symbol"))
            rows.append(quote_row(sig, views[s], am_end=am_end, leak=leak))
        print(f"{day} v11 kept_events={events_n} signals={len(rows)} last_et={last_et}", flush=True)
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


def save_v11_day_cache(path: Path, body: dict[str, Any]) -> None:
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

"""V2 event-level HIGH[k] cross. Frozen MA/BB/RCI/Volume/Board/Fill/C14. Parallelism=1. No live WS."""
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
from research.am_entry_profit_improvement.labels import simulate_current_exit
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import (
    _bare,
    capture_event_epoch,
    iter_push,
    record_event_stamp,
)
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import (
    CACHE,
    _Buf,
    _board_row,
    _f,
    _path_mfe_mae,
    _snap_at,
    _up_first,
)
from research.simple_tech_entry_family.stages import (
    attach_indicators,
    board_support,
    cost_exceed,
    evaluable,
    execution_eligible,
    forward_pack,
    price_action,
    pullback_setup,
    reversal_rci,
    setup_ready,
    trend_up,
    volume_confirm,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

V2_CACHE = CACHE / "v2"
CONTINUOUS_STATES = {"CONTINUOUS_TRADING", "LEGACY_QUOTE_ONLY"}


def _valid_px(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]):
        return False
    if bool(board["special"][i]):
        return False
    st = str(board["board_execution_state"][i] or "")
    if st not in CONTINUOUS_STATES:
        return False
    p = float(board["px"][i])
    return p == p and p > 0.0


def _event_snap(board: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    bid = float(board["bid"][i])
    ask = float(board["ask"][i])
    spread = None
    if bid == bid and ask == ask and bid > 0 and ask > 0:
        mid = (ask + bid) / 2.0
        if mid > 0:
            spread = (ask - bid) / mid * 10000.0
    return {
        "ok": True,
        "i": i,
        "t": float(board["t"][i]),
        "bid": bid,
        "ask": ask,
        "bid_qty": float(board["bid_qty"][i]),
        "ask_qty": float(board["ask_qty"][i]),
        "special": bool(board["special"][i]),
        "fresh_sec": float(board["fresh_sec"][i]),
        "executable": bool(board["executable"][i]),
        "state": str(board["board_execution_state"][i] or ""),
        "px": float(board["px"][i]),
        "spread_bps": spread,
    }


def _apply_fill_exit(
    rec: dict[str, Any],
    board: dict[str, np.ndarray],
    *,
    t0: float,
    limit: float,
    am_end: float,
    day: str,
    symbol: str,
) -> None:
    fill = standalone_fill(
        board,
        t0=float(t0),
        wait_sec=float(DEV_WAIT_SEC),
        limit_price=float(limit),
        sess_end=float(am_end),
    )
    rec["WOULD_FILL"] = bool(fill.get("WOULD_FILL"))
    rec["fill_t"] = fill.get("fill_t")
    rec["fill_price"] = fill.get("fill_price")
    rec["nonfill_class"] = fill.get("nonfill_class")
    rec["limit"] = float(limit)
    if rec["WOULD_FILL"] and rec["fill_t"] is not None and rec["fill_price"] is not None:
        ex = simulate_current_exit(
            board,
            date=day,
            symbol=symbol,
            session="AM",
            fill_t=float(rec["fill_t"]),
            fill_px=float(rec["fill_price"]),
        )
        rec["exit_t"] = ex.get("exit_t")
        rec["exit_price"] = ex.get("exit_price")
        rec["exit_reason"] = ex.get("exit_reason")
        rec["pnl_yen_100"] = ex.get("pnl_yen_100")
        mfe, mae = _path_mfe_mae(board, float(rec["fill_t"]), float(rec["fill_price"]), float(am_end))
        rec["mfe_path"] = mfe
        rec["mae_path"] = mae
    else:
        rec["exit_t"] = None
        rec["exit_price"] = None
        rec["exit_reason"] = None
        rec["pnl_yen_100"] = None
        rec["mfe_path"] = None
        rec["mae_path"] = None


def _scan_cross(
    board: dict[str, np.ndarray],
    *,
    watch_start: float,
    watch_end: float,
    high_k: float,
) -> Optional[int]:
    tt = board.get("t")
    if tt is None or int(tt.size) == 0 or not (high_k == high_k):
        return None
    i_lo = int(np.searchsorted(tt, float(watch_start), side="left"))
    i_hi = int(np.searchsorted(tt, float(watch_end), side="left"))
    prev: Optional[float] = None
    for j in range(i_lo - 1, -1, -1):
        if _valid_px(board, j):
            prev = float(board["px"][j])
            break
    for j in range(i_lo, i_hi):
        if not _valid_px(board, j):
            continue
        cur = float(board["px"][j])
        if prev is not None and prev <= float(high_k) + 1e-12 and cur > float(high_k) + 1e-12:
            return j
        prev = cur
    return None


def process_v2_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    spec_sha = str(payload.get("spec_sha") or "")
    t0w = time.perf_counter()
    leak = {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_LABEL_AS_FEATURE_N": 0,
        "UNIVERSE_SKIP_N": 0,
        "NO_EVENT_TIME_N": 0,
    }
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        bufs: dict[str, _Buf] = {s: _Buf() for s in universe}
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
                print(f"{day} v2 stream events={events_n} last_et={last_et}", flush=True)

        nested: list[dict[str, Any]] = []
        setups: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        bar_rows: list[dict[str, Any]] = []
        integ_fail = 0
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            bar_rows.append({"date": day, "symbol": s, **integ, **builders[s].leak})
            if not integ.get("ok"):
                integ_fail += 1
            if int(raw["minute_epoch"].size) == 0:
                continue
            ind = attach_indicators(raw)
            board = bufs[s].view()
            n = int(ind["close"].size)
            ready_idx: list[int] = []
            for i in range(n):
                if not evaluable(i, n):
                    continue
                t_fin = float(ind["finalize_t"][i])
                if t_fin + float(DEV_WAIT_SEC) > am_end + 1e-12:
                    continue
                snap = _snap_at(board, t_fin)
                s1 = trend_up(ind, i)
                s2 = bool(s1) and pullback_setup(ind, i)
                s3 = bool(s2) and reversal_rci(ind, i)
                s4 = bool(s3) and price_action(ind, i)
                s5 = bool(s4) and volume_confirm(ind, i)
                b_ok, bmeta = board_support(snap) if snap.get("ok") else (False, {"board_ok": False, "board_reason": "NO_BOARD"})
                s6 = bool(s5) and bool(b_ok)
                nested.append(
                    {
                        "date": day,
                        "symbol": s,
                        "bar_minute": float(ind["minute_epoch"][i]),
                        "s0": True,
                        "s1": bool(s1),
                        "s2": bool(s2),
                        "s3": bool(s3),
                        "s4": bool(s4),
                        "s5": bool(s5),
                        "s6": bool(s6),
                        "board_reason": bmeta.get("board_reason"),
                    }
                )
                if setup_ready(ind, i):
                    ready_idx.append(i)

            for i in ready_idx:
                high_k = float(ind["high"][i])
                ema9_k = float(ind["ema9"][i])
                bb_k = float(ind["bb_upper"][i])
                close_k = float(ind["close"][i])
                watch_start = float(ind["finalize_t"][i])
                if i + 1 < n and float(ind["finalize_t"][i + 1]) == float(ind["finalize_t"][i + 1]):
                    watch_end = float(ind["finalize_t"][i + 1])
                else:
                    watch_end = float(am_end)
                fwd = forward_pack(ind, i)
                setup = {
                    "date": day,
                    "session": "AM",
                    "symbol": s,
                    "i": i,
                    "bar_minute": float(ind["minute_epoch"][i]),
                    "setup_t": watch_start,
                    "watch_end": watch_end,
                    "high_k": high_k,
                    "ema9_k": ema9_k if ema9_k == ema9_k else None,
                    "bb_upper_k": bb_k if bb_k == bb_k else None,
                    "close": close_k,
                    "volume": float(ind["volume"][i]),
                    "rci9": float(ind["rci9"][i]) if ind["rci9"][i] == ind["rci9"][i] else None,
                    "rci9_prev": float(ind["rci9"][i - 1]) if i >= 1 and ind["rci9"][i - 1] == ind["rci9"][i - 1] else None,
                    "triggered": False,
                    "expired": False,
                    "status": "",
                    **fwd,
                }
                j = _scan_cross(board, watch_start=watch_start, watch_end=watch_end, high_k=high_k)
                if j is None:
                    setup["expired"] = True
                    setup["status"] = "SETUP_EXPIRED"
                    setups.append(setup)
                    continue
                snap = _event_snap(board, j)
                t0 = float(snap["t"])
                px = float(snap["px"])
                setup["triggered"] = True
                setup["trigger_t"] = t0
                setup["trigger_px"] = px
                ema_ok = setup["ema9_k"] is not None and px > float(setup["ema9_k"])
                bb_ok = setup["bb_upper_k"] is not None and px <= float(setup["bb_upper_k"])
                b_ok, bmeta = board_support(snap)
                exec_ok = execution_eligible(snap)
                wait_ok = t0 + float(DEV_WAIT_SEC) <= am_end + 1e-12
                limit = snap.get("bid")
                limit_ok = _f(limit) is not None and float(limit) > 0
                setup["ema_ok"] = bool(ema_ok)
                setup["bb_ok"] = bool(bb_ok)
                setup["board_ok"] = bool(b_ok)
                setup["exec_ok"] = bool(exec_ok)
                setup["wait_ok"] = bool(wait_ok)
                setup["board_reason"] = bmeta.get("board_reason")
                if not ema_ok:
                    setup["status"] = "CROSS_EMA_FAIL"
                    setups.append(setup)
                    continue
                if not bb_ok:
                    setup["status"] = "CROSS_BB_FAIL"
                    setups.append(setup)
                    continue
                if not b_ok:
                    setup["status"] = "BOARD_VETO"
                    setups.append(setup)
                    continue
                if not exec_ok or not wait_ok or not limit_ok:
                    setup["status"] = "EXEC_INELIGIBLE" if not exec_ok else ("SESSION_END_NO_WAIT" if not wait_ok else "NO_LIMIT")
                    setups.append(setup)
                    continue
                up_f, dn_f = _up_first(board, t0, close_k)
                spread = snap.get("spread_bps")
                cex = cost_exceed(fwd.get("fwd_1m"), spread if isinstance(spread, (int, float)) else None)
                rec = {
                    "date": day,
                    "session": "AM",
                    "symbol": s,
                    "i": i,
                    "bar_minute": float(ind["minute_epoch"][i]),
                    "setup_t": watch_start,
                    "t0": t0,
                    "signal_time": t0,
                    "trigger_px": px,
                    "open": float(ind["open"][i]),
                    "high": high_k,
                    "low": float(ind["low"][i]),
                    "close": close_k,
                    "volume": float(ind["volume"][i]),
                    "ema9": setup["ema9_k"],
                    "ema21": float(ind["ema21"][i]) if ind["ema21"][i] == ind["ema21"][i] else None,
                    "bb_upper": setup["bb_upper_k"],
                    "bb_lower": float(ind["bb_lower"][i]) if ind["bb_lower"][i] == ind["bb_lower"][i] else None,
                    "rci9": setup["rci9"],
                    "rci9_prev": setup["rci9_prev"],
                    "up_vol": float(ind["up_vol"][i]),
                    "down_vol": float(ind["down_vol"][i]),
                    "ask_vol": float(ind["ask_vol"][i]),
                    "bid_vol": float(ind["bid_vol"][i]),
                    "bid": snap.get("bid"),
                    "ask": snap.get("ask"),
                    "bid_qty": snap.get("bid_qty"),
                    "ask_qty": snap.get("ask_qty"),
                    "spread_bps": spread,
                    "board_reason": bmeta.get("board_reason"),
                    "up_first": int(up_f),
                    "down_first": int(dn_f),
                    "cost_exceed": bool(cex),
                    "s8": False,
                    "s9": False,
                    "s10": False,
                    **fwd,
                }
                _apply_fill_exit(rec, board, t0=t0, limit=float(limit), am_end=am_end, day=day, symbol=s)
                setup["status"] = "PENDING"
                setup["limit"] = float(limit)
                setup["WOULD_FILL"] = rec.get("WOULD_FILL")
                setups.append(setup)
                signals.append(rec)

        print(
            f"{day} v2 events={events_n} nested={len(nested)} setup={len(setups)} sig={len(signals)} bars_fail={integ_fail}",
            flush=True,
        )
        del bufs, builders
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "spec_sha": spec_sha,
            "events_n": events_n,
            "last_et": last_et,
            "last_seq": last_seq,
            "nested": nested,
            "setups": setups,
            "signals": signals,
            "bar_rows": bar_rows,
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


def save_v2_day_cache(path: Path, body: dict[str, Any]) -> None:
    from research.am_entry_profit_improvement.publish import json_sanitize

    path.parent.mkdir(parents=True, exist_ok=True)
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
            "bar_rows": body.get("bar_rows"),
            "nested": body.get("nested"),
            "setups": body.get("setups"),
            "signals": body.get("signals"),
            "blocker": body.get("blocker"),
        }
    )
    path.write_text(__import__("json").dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")

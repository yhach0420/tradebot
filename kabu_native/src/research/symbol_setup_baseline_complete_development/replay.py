"""One development replay. Imports the frozen entry, fill, portfolio, and thesis rules."""
from __future__ import annotations

import gc
from pathlib import Path
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import capture_event_epoch, iter_push, record_event_stamp
from research.entry_execution_feasibility.fill import standalone_fill
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.simple_tech_entry_family.harvest import _Buf, _bare, _board_row, _f, _snap_at
from research.simple_tech_entry_family.portfolio import portfolio_replay
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
from research.simple_tech_redesign.v28_harvest import first_causal_bid, last_session_bid
from research.symbol_setup_baseline_complete_strategy_precommit.runner import account, loss_reason, thesis_live
from small_paper.v1r_live_dual_lane import session_end_for_position


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _exit_from_bid(found: dict[str, Any], reason: str, *, entry_px: float) -> dict[str, Any]:
    pnl = account(float(entry_px), float(found["exit_bid"]))
    return {
        "exit_t": float(found["exit_t"]),
        "exit_price": float(found["exit_bid"]),
        "exit_reason": reason,
        "pnl_yen_100": float(pnl["net_pnl_yen"]),
        "closed": True,
    }


def thesis_exit(ind: dict[str, np.ndarray], board: dict[str, np.ndarray], *, fill_t: float, fill_px: float, am_end: float) -> dict[str, Any]:
    """First new completed bar after the fill. The signal bar is not reused."""
    fin = ind["finalize_t"]
    n = int(fin.size)
    start = int(np.searchsorted(fin, float(fill_t), side="right"))
    first_obs = None
    lost_at = None
    lost_reason = None
    for i in range(start, n):
        obs_t = float(fin[i])
        if obs_t <= float(fill_t) + 1e-12:
            continue
        if obs_t > float(am_end) + 1e-12:
            break
        if first_obs is None:
            first_obs = obs_t
        e9 = float(ind["ema9"][i]) if _finite(ind["ema9"][i]) else None
        e21 = float(ind["ema21"][i]) if _finite(ind["ema21"][i]) else None
        e21p = float(ind["ema21"][i - 3]) if i >= 3 and _finite(ind["ema21"][i - 3]) else None
        if e9 is None or e21 is None or e21p is None:
            found = first_causal_bid(board, {}, event_t=obs_t, fill_px=float(fill_px), sess_end=float(am_end), leak={})
            if not found.get("miss"):
                out = _exit_from_bid(found, "FAIL_CLOSE_INVALID_DATA", entry_px=float(fill_px))
                out["thesis_first_observation"] = first_obs
                out["thesis_lost_at"] = None
                out["thesis_loss_reason"] = None
                return out
            break
        if thesis_live(e9, e21, e21p):
            continue
        lost_at = obs_t
        lost_reason = loss_reason(e9, e21, e21p)
        found = first_causal_bid(board, {}, event_t=obs_t, fill_px=float(fill_px), sess_end=float(am_end), leak={})
        if not found.get("miss"):
            out = _exit_from_bid(found, lost_reason, entry_px=float(fill_px))
            out["thesis_first_observation"] = first_obs
            out["thesis_lost_at"] = lost_at
            out["thesis_loss_reason"] = lost_reason
            return out
        break
    found = last_session_bid(board, {}, fill_t=float(fill_t), sess_end=float(am_end), leak={})
    if not found.get("miss"):
        out = _exit_from_bid(found, "SESSION_FAIL_CLOSE", entry_px=float(fill_px))
        out["thesis_first_observation"] = first_obs
        out["thesis_lost_at"] = lost_at
        out["thesis_loss_reason"] = lost_reason
        return out
    return {
        "exit_t": None,
        "exit_price": None,
        "exit_reason": "UNCLOSED",
        "pnl_yen_100": None,
        "closed": False,
        "thesis_first_observation": first_obs,
        "thesis_lost_at": lost_at,
        "thesis_loss_reason": lost_reason,
    }


def _day(day: str, capture: Path, symbols: list[str]) -> dict[str, Any]:
    am_start = float(hm_epoch(day, 9, 0))
    am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
    bufs = {s: _Buf() for s in symbols}
    builders = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in symbols}
    uni = set(symbols)
    counts = {"s5": 0, "s6": 0, "s7": 0}
    signals: list[dict[str, Any]] = []
    for rec in iter_push(capture):
        sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
        if not sym or sym not in uni:
            continue
        pay = dict(rec.get("payload") or rec.get("original_payload") or {})
        et = capture_event_epoch(rec, pay)
        if et is None or float(et) < am_start - 120.0 or float(et) > am_end + 2.0:
            continue
        recv = record_event_stamp(rec)
        if recv:
            pay["received_at"] = recv
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
    for sym in symbols:
        builders[sym].close_session()
        raw = builders[sym].as_arrays()
        bar_integrity(raw, am_start=am_start, am_end=am_end)
        if int(raw["minute_epoch"].size) == 0:
            continue
        ind = attach_indicators(raw)
        board = bufs[sym].view()
        n = int(ind["close"].size)
        for i in range(n):
            if not evaluable(i, n):
                continue
            t0 = float(ind["finalize_t"][i])
            if t0 + float(DEV_WAIT_SEC) > am_end + 1e-12:
                continue
            snap = _snap_at(board, t0)
            passed = trend_up(ind, i) and pullback_setup(ind, i) and reversal_rci(ind, i) and price_action(ind, i) and volume_confirm(ind, i)
            if not passed:
                continue
            counts["s5"] += 1
            board_ok, _meta = board_support(snap) if snap.get("ok") else (False, {})
            if not board_ok:
                continue
            counts["s6"] += 1
            if not (snap.get("ok") and execution_eligible(snap)):
                continue
            limit = snap.get("bid")
            if _f(limit) is None or float(limit) <= 0:
                continue
            counts["s7"] += 1
            fill = standalone_fill(board, t0=t0, wait_sec=float(DEV_WAIT_SEC), limit_price=float(limit), sess_end=float(am_end))
            row_out = {
                "date": day,
                "session": "AM",
                "symbol": sym,
                "signal_time": t0,
                "t0": t0,
                "pending_time": t0,
                "entry_source": "SIMPLE_TECH_V1_PASSIVE_BID_W5",
                "limit": float(limit),
                "WOULD_FILL": bool(fill.get("WOULD_FILL")),
                "fill_t": fill.get("fill_t"),
                "fill_price": fill.get("fill_price"),
                "exit_t": None,
                "exit_price": None,
                "exit_reason": None,
                "pnl_yen_100": None,
                "thesis_first_observation": None,
                "thesis_lost_at": None,
                "thesis_loss_reason": None,
            }
            if row_out["WOULD_FILL"] and row_out["fill_t"] is not None and row_out["fill_price"] is not None:
                ex = thesis_exit(ind, board, fill_t=float(row_out["fill_t"]), fill_px=float(row_out["fill_price"]), am_end=am_end)
                row_out.update(ex)
            signals.append(row_out)
    return {"date": day, "counts": counts, "signals": signals}


def replay(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    from research.anchor_vs_event_driven.run_comparison import find_capture_dir

    counts = {"s5": 0, "s6": 0, "s7": 0}
    signals: list[dict[str, Any]] = []
    for sess in sessions:
        day = str(sess["date"])
        if day > "20260910":
            raise RuntimeError("surface_past_cutoff")
        cap = find_capture_dir(day)
        if cap is None:
            raise RuntimeError(f"missing_capture:{day}")
        symbols = [str(s) for s in list(sess["symbols"])]
        one = _day(day, Path(cap), symbols)
        print(f"{day} s5={one['counts']['s5']} s6={one['counts']['s6']} s7={one['counts']['s7']}", flush=True)
        for key in counts:
            counts[key] += int(one["counts"][key])
        signals.extend(one["signals"])
        gc.collect()
    port = portfolio_replay(signals)
    return {"counts": counts, "signals": signals, "portfolio": port}

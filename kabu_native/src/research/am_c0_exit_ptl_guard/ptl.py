"""Causal PTL guard on C14 executable continuous-board quotes. No MFE oracle."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_c0_exit_ptl_guard import (
    EARLIEST_TRIGGER_SEC,
    EXIT_REASON,
    LATEST_TRIGGER_SEC,
)
from research.am_entry_profit_improvement.labels import simulate_current_exit
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.e1_x35_passive_exit.paths import _valid_bid
from research.entry_sequence_representation.sequence import itayose_mask, special_mask
from replay.pnl_yen import compute_pnl_yen_100
from small_paper.v1r_live_dual_lane import session_end_for_position

PNL_EPS = 1e-12


def _yen(fill_px: float, bid: float) -> float:
    return float(compute_pnl_yen_100(float(fill_px), float(bid)))


def _empty_leak() -> dict[str, int]:
    return {
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "ITAYOSE_EXIT_USE_N": 0,
        "SPECIAL_BOARD_EXIT_USE_N": 0,
        "INVALID_QUOTE_EXIT_USE_N": 0,
        "ORACLE_MFE_EXIT_USE_N": 0,
        "FUTURE_EXIT_SIGNAL_USE_N": 0,
    }


def simulate_ptl_then_c14(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    fill_t: float,
    fill_px: float,
    c14: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Fire PTL on first valid 120<=t<600 armed-then-nonpositive quote; else frozen C14."""
    leak = _empty_leak()
    if c14 is None:
        c14 = simulate_current_exit(
            board,
            date=str(date),
            symbol=str(symbol),
            session=str(session),
            fill_t=float(fill_t),
            fill_px=float(fill_px),
        )
    c14_t = _f(c14.get("exit_t"))
    c14_px = _f(c14.get("exit_price"))
    c14_pnl = _f(c14.get("pnl_yen_100"))
    c14_reason = str(c14.get("exit_reason") or "")
    sess_end = float(session_end_for_position(date=str(date), session=str(session), fill_time=float(fill_t)))
    t = board.get("t")
    out_c14 = {
        "c14_exit_t": c14_t,
        "c14_exit_price": c14_px,
        "c14_exit_reason": c14_reason,
        "c14_pnl_yen_100": c14_pnl,
        "profit_armed": False,
        "ptl_triggered": False,
        "ptl_t": None,
        "ptl_price": None,
        "ptl_pnl_yen_100": None,
        "ptl_holding_sec": None,
        "first_profit_t": None,
        "first_profit_yen": None,
        "SESSION_END": sess_end,
        "integrity": leak,
    }
    if t is None or int(getattr(t, "size", 0) or 0) == 0 or c14_t is None:
        out_c14.update(
            {
                "ok": False,
                "blocker": "NO_BOARD_OR_C14",
                "exit_t": c14_t,
                "exit_price": c14_px,
                "exit_reason": c14_reason,
                "pnl_yen_100": c14_pnl,
            }
        )
        return out_c14

    itay = itayose_mask(board)
    spec = special_mask(board)
    n = int(t.size)
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    profit_armed = False
    first_profit_t = None
    first_profit_yen = None
    ptl = None
    for i in range(i0, n):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > sess_end + 1e-12:
            break
        if ti > float(c14_t) + 1e-12:
            break
        off = ti - float(fill_t)
        if off + 1e-12 >= float(LATEST_TRIGGER_SEC) and ptl is None:
            break
        if i < itay.size and bool(itay[i]):
            leak["ITAYOSE_SKIP_N"] += 1
            continue
        if i < spec.size and bool(spec[i]):
            leak["SPECIAL_SKIP_N"] += 1
            continue
        if not _valid_bid(board, i):
            leak["INVALID_SKIP_N"] += 1
            continue
        bid = float(board["bid"][i])
        yen = _yen(float(fill_px), bid)
        if yen > PNL_EPS:
            profit_armed = True
            if first_profit_t is None:
                first_profit_t = ti
                first_profit_yen = yen
        if off + 1e-12 < float(EARLIEST_TRIGGER_SEC):
            continue
        if off + 1e-12 >= float(LATEST_TRIGGER_SEC):
            continue
        if profit_armed and yen <= 0.0:
            ptl = {
                "exit_t": ti,
                "exit_price": bid,
                "exit_reason": EXIT_REASON,
                "pnl_yen_100": round(yen, 4),
                "ptl_holding_sec": float(off),
                "ptl_pnl_yen_100": round(yen, 4),
            }
            break

    out_c14["profit_armed"] = bool(profit_armed)
    out_c14["first_profit_t"] = first_profit_t
    out_c14["first_profit_yen"] = first_profit_yen
    if ptl is None:
        out_c14.update(
            {
                "ok": True,
                "blocker": None,
                "ptl_triggered": False,
                "exit_t": c14_t,
                "exit_price": c14_px,
                "exit_reason": c14_reason,
                "pnl_yen_100": c14_pnl,
            }
        )
        return out_c14
    out_c14.update(
        {
            "ok": True,
            "blocker": None,
            "ptl_triggered": True,
            "ptl_t": ptl["exit_t"],
            "ptl_price": ptl["exit_price"],
            "ptl_pnl_yen_100": ptl["ptl_pnl_yen_100"],
            "ptl_holding_sec": ptl["ptl_holding_sec"],
            "exit_t": ptl["exit_t"],
            "exit_price": ptl["exit_price"],
            "exit_reason": ptl["exit_reason"],
            "pnl_yen_100": ptl["pnl_yen_100"],
        }
    )
    return out_c14


def apply_e1_exits(rows: list[dict[str, Any]], e1_by_key: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Replace EXIT fields on C0-eligible fills only. CURRENT rows keep C14."""
    out = []
    miss = 0
    for r in rows:
        rec = dict(r)
        rec["_c14_exit_t"] = r.get("exit_t")
        rec["_c14_exit_price"] = r.get("exit_price")
        rec["_c14_exit_reason"] = r.get("exit_reason")
        rec["_c14_pnl_yen_100"] = r.get("pnl_yen_100")
        rec["_e1_ptl_triggered"] = False
        if rec.get("_aug_eligible") and int(rec.get("Y_FILL5") or 0) == 1:
            key = str(rec.get("_row_key") or row_key(rec))
            e1 = e1_by_key.get(key)
            if e1 is None or not e1.get("ok"):
                miss += 1
                rec["_e1_miss"] = True
            elif bool(e1.get("ptl_triggered")):
                rec["exit_t"] = e1.get("exit_t")
                rec["exit_price"] = e1.get("exit_price")
                rec["exit_reason"] = e1.get("exit_reason")
                rec["pnl_yen_100"] = e1.get("pnl_yen_100")
                rec["_e1_ptl_triggered"] = True
                rec["_e1_ptl_holding_sec"] = e1.get("ptl_holding_sec")
        out.append(rec)
    return out, miss

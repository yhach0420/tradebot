"""Force CONT_EXIT_600 into existing Arch E CONT_EXTEND_750. No new 750 logic. No PTL."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_c0_exit_continuation_reassessment import (
    CONT_EXIT_600,
    DECISION_SEC,
    E1,
    E2,
)
from research.am_entry_profit_improvement.labels import _with_mid, simulate_current_exit
from research.canonical_entry_performance_rebase.analyze import row_key
from research.e1_x35_passive_exit.paths import _valid_bid, build_path
from research.entry_sequence_representation.sequence import itayose_mask, special_mask
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import last_valid_bid_at_or_before
from research.v1r_exit_v2_asymmetric.policy import apply_architecture
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from replay.pnl_yen import compute_pnl_yen_100
from small_paper.v1r_exit_v2_contract import frozen_guard
from small_paper.v1r_live_dual_lane import session_end_for_position

PNL_EPS = 1e-12
ALWAYS_EXTEND = {"id": "ALWAYS_EXTEND", "kind": "always"}


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
        "PTL_REUSE_N": 0,
    }


def ever_profitable_pre600(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
) -> tuple[bool, Optional[float], Optional[float], dict[str, int]]:
    """Causal: any valid executable yen > 0 with holding_sec < 600. No MFE."""
    leak = _empty_leak()
    t = board.get("t")
    if t is None or int(getattr(t, "size", 0) or 0) == 0:
        return False, None, None, leak
    itay = itayose_mask(board)
    spec = special_mask(board)
    n = int(t.size)
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    armed = False
    first_t = None
    first_yen = None
    for i in range(i0, n):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        off = ti - float(fill_t)
        if off + 1e-12 >= float(DECISION_SEC):
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
        yen = float(compute_pnl_yen_100(float(fill_px), bid))
        if yen > PNL_EPS:
            armed = True
            if first_t is None:
                first_t = ti
                first_yen = yen
    return bool(armed), first_t, first_yen, leak


def _finalize_policy(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    pol: dict[str, Any],
) -> dict[str, Any]:
    reason = "SESSION_CLOSE"
    exit_t: Optional[float] = None
    exit_px: Optional[float] = None
    if pol.get("ok"):
        reason = str(pol.get("reason") or "ARCH_E")
        exit_t = float(pol["exit_time"])
        ret = float(pol.get("exit_ret_bps") or 0.0)
        exit_px = float(fill_px) * (1.0 + ret / 10000.0)
        if exit_t > float(sess_end) + 1e-12:
            reason = "SESSION_CLOSE"
            exit_t = None
            exit_px = None
    if exit_t is None or exit_px is None:
        found = last_valid_bid_at_or_before(board, until_t=float(sess_end), after_t=float(fill_t))
        if found is None:
            found = last_valid_bid_at_or_before(board, until_t=float(sess_end), after_t=None)
        if found is None:
            exit_t = float(fill_t)
            exit_px = float(fill_px)
            reason = "SESSION_CLOSE"
        else:
            exit_t, exit_px = found
            reason = "SESSION_CLOSE"
    pnl = round(float(compute_pnl_yen_100(float(fill_px), float(exit_px))), 4)
    return {
        "exit_t": float(exit_t),
        "exit_price": float(exit_px),
        "exit_reason": reason,
        "pnl_yen_100": pnl,
    }


def simulate_c14_and_extend(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    fill_t: float,
    fill_px: float,
) -> dict[str, Any]:
    """C14 baseline + existing Arch E ALWAYS_EXTEND 750 branch. No PTL."""
    c14 = simulate_current_exit(
        board,
        date=str(date),
        symbol=str(symbol),
        session=str(session),
        fill_t=float(fill_t),
        fill_px=float(fill_px),
    )
    sess_end = float(session_end_for_position(date=str(date), session=str(session), fill_time=float(fill_t)))
    ever, first_t, first_yen, leak = ever_profitable_pre600(
        board, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=sess_end
    )
    board2 = _with_mid(board)
    path = build_path(board2, entry_price=float(fill_px), entry_t=float(fill_t), sess_end=float(sess_end))
    ext = dict(c14)
    if path.get("ok"):
        bundle = build_trade_bundle(
            {
                "date": date,
                "symbol": symbol,
                "session": session,
                "fill_time": float(fill_t),
                "fill_price": float(fill_px),
                "anchor_id": "C0_CONT_REASSESSMENT",
            },
            path,
            board2,
        )
        pol = apply_architecture(bundle, arch="E", guard=frozen_guard(), cont_rule=ALWAYS_EXTEND)
        ext = _finalize_policy(board, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=sess_end, pol=pol)
    c14_reason = str(c14.get("exit_reason") or "")
    is_cont600 = c14_reason == CONT_EXIT_600
    e1_force = bool(is_cont600 and ever)
    e2_force = bool(is_cont600)
    e1 = dict(ext) if e1_force else dict(c14)
    e2 = dict(ext) if e2_force else dict(c14)
    return {
        "ok": True,
        "blocker": None,
        "c14_exit_t": c14.get("exit_t"),
        "c14_exit_price": c14.get("exit_price"),
        "c14_exit_reason": c14_reason,
        "c14_pnl_yen_100": c14.get("pnl_yen_100"),
        "ever_profitable_pre600": bool(ever),
        "first_profit_t": first_t,
        "first_profit_yen": first_yen,
        "extend_exit_t": ext.get("exit_t"),
        "extend_exit_price": ext.get("exit_price"),
        "extend_exit_reason": ext.get("exit_reason"),
        "extend_pnl_yen_100": ext.get("pnl_yen_100"),
        "pnl_at_600": c14.get("pnl_yen_100") if is_cont600 else None,
        "e1_forced": e1_force,
        "e2_forced": e2_force,
        "e1_exit_t": e1.get("exit_t"),
        "e1_exit_price": e1.get("exit_price"),
        "e1_exit_reason": e1.get("exit_reason"),
        "e1_pnl_yen_100": e1.get("pnl_yen_100"),
        "e2_exit_t": e2.get("exit_t"),
        "e2_exit_price": e2.get("exit_price"),
        "e2_exit_reason": e2.get("exit_reason"),
        "e2_pnl_yen_100": e2.get("pnl_yen_100"),
        "SESSION_END": sess_end,
        "integrity": leak,
    }


def apply_arm_exits(
    rows: list[dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    *,
    arm_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Patch C0-eligible fills only. CURRENT keeps labeled C14."""
    prefix = "e1" if arm_id == E1 else "e2"
    force_key = "e1_forced" if arm_id == E1 else "e2_forced"
    out = []
    miss = 0
    for r in rows:
        rec = dict(r)
        rec["_c14_exit_t"] = r.get("exit_t")
        rec["_c14_exit_price"] = r.get("exit_price")
        rec["_c14_exit_reason"] = r.get("exit_reason")
        rec["_c14_pnl_yen_100"] = r.get("pnl_yen_100")
        rec["_forced_extend"] = False
        if rec.get("_aug_eligible") and int(rec.get("Y_FILL5") or 0) == 1:
            key = str(rec.get("_row_key") or row_key(rec))
            got = by_key.get(key)
            if got is None or not got.get("ok"):
                miss += 1
                rec["_e_miss"] = True
            elif bool(got.get(force_key)):
                rec["exit_t"] = got.get(f"{prefix}_exit_t")
                rec["exit_price"] = got.get(f"{prefix}_exit_price")
                rec["exit_reason"] = got.get(f"{prefix}_exit_reason")
                rec["pnl_yen_100"] = got.get(f"{prefix}_pnl_yen_100")
                rec["_forced_extend"] = True
                rec["_ever_profitable_pre600"] = got.get("ever_profitable_pre600")
        out.append(rec)
    return out, miss

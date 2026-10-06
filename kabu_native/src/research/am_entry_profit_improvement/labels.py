"""Standalone REALIZED_ENTRY_UTILITY: W5 corrected fill × current EXIT. No portfolio."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import UTILITY_KEY
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.e1_x35_passive_exit.paths import build_path
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import last_valid_bid_at_or_before
from research.passive_wait_policy_reassessment.analyze import wait_body
from replay.pnl_yen import compute_pnl_yen_100
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from small_paper.v1r_exit_v2_contract import apply_arch_e_to_bundle
from small_paper.v1r_live_dual_lane import session_end_for_position


def _with_mid(board: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out = dict(board)
    if "mid" not in out:
        bid = np.asarray(out["bid"], dtype=float)
        ask = np.asarray(out["ask"], dtype=float)
        mid = np.full(bid.shape, np.nan, dtype=float)
        ok = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > 0)
        mid[ok] = (bid[ok] + ask[ok]) / 2.0
        out["mid"] = mid
    return out


def simulate_current_exit(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    fill_t: float,
    fill_px: float,
) -> dict[str, Any]:
    """Frozen C14 Arch E. Future prices are label-only."""
    sess_end = session_end_for_position(date=date, session=session, fill_time=float(fill_t))
    board2 = _with_mid(board)
    path = build_path(board2, entry_price=float(fill_px), entry_t=float(fill_t), sess_end=float(sess_end))
    reason = "SESSION_CLOSE"
    exit_t: Optional[float] = None
    exit_px: Optional[float] = None
    if path.get("ok"):
        bundle = build_trade_bundle(
            {
                "date": date,
                "symbol": symbol,
                "session": session,
                "fill_time": float(fill_t),
                "fill_price": float(fill_px),
                "anchor_id": "STANDALONE_UTILITY",
            },
            path,
            board2,
        )
        pol = apply_arch_e_to_bundle(bundle)
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


def join_harvest(rows: list[dict[str, Any]], harvest: list[dict[str, Any]]) -> dict[str, int]:
    by = {row_key(h): h for h in harvest}
    miss = y_mismatch = 0
    for r in rows:
        h = by.get(row_key(r))
        if h is None:
            miss += 1
            r["t0"] = None
            r["limit"] = None
            r["fill_t"] = None
            r["fill_price"] = None
            r["WOULD_FILL5"] = False
            continue
        w = wait_body(h, "W5")
        filled = bool(w.get("WOULD_FILL"))
        r["t0"] = h.get("t0")
        r["limit"] = h.get("limit")
        r["fill_t"] = w.get("fill_t") if filled else None
        r["fill_price"] = w.get("fill_price") if filled else None
        r["WOULD_FILL5"] = filled
        yi = int(r.get("Y_FILL5") or 0) == 1
        if yi != filled:
            y_mismatch += 1
    return {"JOIN_MISS_N": miss, "Y_FILL5_HARVEST_MISMATCH_N": y_mismatch}


def apply_utility(rows: list[dict[str, Any]], exits: dict[str, dict[str, Any]]) -> dict[str, int]:
    """nonfill=0. Fill uses standalone current-EXIT pnl. Portfolio interactions excluded."""
    miss_exit = fill_n = zero_n = 0
    for r in rows:
        yi = int(r.get("Y_FILL5") or 0) == 1
        if not yi:
            r[UTILITY_KEY] = 0.0
            r["exit_t"] = None
            r["exit_price"] = None
            r["exit_reason"] = None
            r["pnl_yen_100"] = 0.0
            zero_n += 1
            continue
        ex = exits.get(row_key(r)) or {}
        pnl = _f(ex.get("pnl_yen_100"))
        if pnl is None:
            miss_exit += 1
            r[UTILITY_KEY] = None
            r["exit_t"] = None
            r["exit_price"] = None
            r["exit_reason"] = None
            r["pnl_yen_100"] = None
            continue
        r[UTILITY_KEY] = float(pnl)
        r["exit_t"] = ex.get("exit_t")
        r["exit_price"] = ex.get("exit_price")
        r["exit_reason"] = ex.get("exit_reason")
        r["pnl_yen_100"] = float(pnl)
        fill_n += 1
    return {
        "UTILITY_FILL_N": fill_n,
        "UTILITY_ZERO_N": zero_n,
        "UTILITY_EXIT_MISS_N": miss_exit,
        "TARGET_CONTAMINATION_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
    }

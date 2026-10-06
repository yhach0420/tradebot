"""1-second persistent continuous-mid path. Same-session only. No event-count MFE."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import PREOPEN_ITAYOSE_SIGNS, SPECIAL_QUOTE_SIGNS
from research.executable_target_v2_coverage_audit.session_sot import (
    plus_sec_hm,
    tse_continuous_at_hm,
    tse_phase_hm,
)
from research.target_price_contract_v3.marks import ITAYOSE_STATES, SPECIAL_STATES
from research.target_price_contract_v4.persist import CONT_PHASES
from research.entry_target_architecture import GRID_POINTS, HORIZON_SEC


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def session_lo_for_hm(day: str, h: int, m: int) -> Optional[float]:
    from research.anchor_timing_robustness.grid import hm_epoch

    phase = tse_phase_hm(h, m)
    if phase == "AM_CONTINUOUS":
        return float(hm_epoch(day, 9, 0))
    if phase in {"PM_CONTINUOUS_ZARABA", "PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}:
        return float(hm_epoch(day, 12, 30))
    return None


def valid_mid_series(board: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    t = board.get("t")
    if t is None or t.size == 0:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    n = int(t.size)
    bid = np.asarray(board["bid"][:n], dtype=float)
    ask = np.asarray(board["ask"][:n], dtype=float)
    good = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid + 1e-12)
    spec = board.get("special")
    if spec is not None and int(spec.size) >= n:
        good &= ~np.asarray(spec[:n], dtype=bool)
    st = board.get("board_execution_state")
    if st is not None and int(st.size) >= n:
        s = np.asarray(st[:n], dtype=object)
        blocked = np.zeros(n, dtype=bool)
        for name in ITAYOSE_STATES | SPECIAL_STATES:
            blocked |= s == name
        good &= ~blocked
    ask_s = board.get("ask_sign")
    bid_s = board.get("bid_sign")
    if ask_s is not None and int(ask_s.size) >= n:
        a = np.asarray(ask_s[:n], dtype=object)
        blocked = np.zeros(n, dtype=bool)
        for name in PREOPEN_ITAYOSE_SIGNS | SPECIAL_QUOTE_SIGNS:
            blocked |= a == name
        good &= ~blocked
    if bid_s is not None and int(bid_s.size) >= n:
        b = np.asarray(bid_s[:n], dtype=object)
        blocked = np.zeros(n, dtype=bool)
        for name in PREOPEN_ITAYOSE_SIGNS | SPECIAL_QUOTE_SIGNS:
            blocked |= b == name
        good &= ~blocked
    ts = np.asarray(t[:n], dtype=float)[good]
    mids = (0.5 * (bid + ask))[good]
    return ts, mids


def _t_lo_vec(
    marks: np.ndarray,
    *,
    session_lo: Optional[float],
    reversal_cuts: list[float],
    restart_cuts: list[float],
) -> np.ndarray:
    base = float(session_lo) if session_lo is not None else float("-inf")
    lo = np.full(marks.shape, base, dtype=float)
    cuts = sorted({float(c) for c in list(reversal_cuts) + list(restart_cuts)})
    if not cuts:
        return lo
    arr = np.asarray(cuts, dtype=float)
    i = np.searchsorted(arr, marks, side="right") - 1
    ok = i >= 0
    lo[ok] = np.maximum(lo[ok], arr[i[ok]])
    return lo


def mids_at_marks(
    valid_t: np.ndarray,
    valid_mid: np.ndarray,
    marks: np.ndarray,
    t_lo: np.ndarray,
) -> tuple[np.ndarray, int]:
    out = np.full(marks.shape, np.nan, dtype=float)
    if valid_t.size == 0:
        return out, 0
    i = np.searchsorted(valid_t, marks, side="right") - 1
    ok = i >= 0
    ii = np.where(ok, i, 0)
    vt = valid_t[ii]
    ok = ok & (vt + 1e-12 >= t_lo) & (vt <= marks + 1e-12)
    future_n = int(np.sum(ok & (vt > marks + 1e-12)))
    out[ok] = valid_mid[ii[ok]]
    return out, future_n


def path_targets(
    *,
    day: str,
    h: int,
    m: int,
    t0: float,
    valid_t: np.ndarray,
    valid_mid: np.ndarray,
    reversal_cuts: list[float],
    restart_cuts: list[float],
) -> dict[str, Any]:
    h1, m1 = plus_sec_hm(h, m, HORIZON_SEC)
    p0 = tse_phase_hm(h, m)
    p1 = tse_phase_hm(h1, m1)
    t0_cont = bool(tse_continuous_at_hm(h, m))
    t1_cont = bool(tse_continuous_at_hm(h1, m1))
    session_lo = session_lo_for_hm(day, h, m)
    marks = t0 + np.arange(GRID_POINTS, dtype=float)
    t_lo = _t_lo_vec(marks, session_lo=session_lo, reversal_cuts=reversal_cuts, restart_cuts=restart_cuts)
    mids, future_n = mids_at_marks(valid_t, valid_mid, marks, t_lo)
    m0 = float(mids[0]) if np.isfinite(mids[0]) and float(mids[0]) > 0 else None
    same_session = bool(p0 in CONT_PHASES and p0 == p1)
    session_carry = bool((not same_session) or p0 not in CONT_PHASES)
    path_complete = bool(m0 is not None and bool(np.all(np.isfinite(mids))))
    t0_v = t1_v = t2_v = t3_v = t4_v = None
    native_t4 = None
    if m0 is not None:
        r = mids / m0 - 1.0
        if np.isfinite(mids[600]):
            t0_v = float(r[600])
        if np.isfinite(mids[120]):
            native_t4 = float(r[120])
        if path_complete:
            mfe = float(np.max(r[1:]))
            mn = float(np.min(r[1:]))
            mae = float(min(0.0, mn))
            t1_v = mfe
            t2_v = mn
            t3_v = float(mfe + mae)
            t4_v = float(r[120])
    closing = p1 in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}
    common = bool(
        path_complete and t0_cont and t1_cont and (not session_carry) and (not closing) and m0 is not None
    )
    return {
        "m0": m0,
        "T0": t0_v,
        "T1": t1_v,
        "T2": t2_v,
        "T3": t3_v,
        "T4": t4_v,
        "T4_NATIVE": native_t4,
        "path_complete": path_complete,
        "tse_t0_cont": t0_cont,
        "tse_t1_cont": t1_cont,
        "t0_phase": p0,
        "t1_phase": p1,
        "session_carry": session_carry,
        "closing_auction_endpoint": closing,
        "future_event_use": bool(future_n > 0),
        "future_event_n": int(future_n),
        "common_cohort": common,
        "used_itayose": False,
        "used_special": False,
    }

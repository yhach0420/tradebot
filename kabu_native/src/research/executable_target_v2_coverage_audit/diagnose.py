"""Lookup probe + canonical first-fail. Does not change PRIMARY eligibility."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.executable_target_v2_b_threshold.contract import last_executable_mid
from research.executable_target_v2_coverage_audit import CAPTURE_GAP_LAG_SEC, HORIZON_SEC
from research.executable_target_v2_coverage_audit.session_sot import (
    tse_continuous_at_hm,
    tse_phase_hm,
)

CANONICAL_NULL_REASONS = (
    "T0_OUTSIDE_SESSION",
    "T0_NON_EXECUTABLE",
    "T0_STALE",
    "T0_NO_VALID_MID",
    "T600_OUTSIDE_CONTINUOUS",
    "T600_CLOSING_AUCTION",
    "T600_NO_EVENT",
    "T600_NON_EXECUTABLE",
    "T600_STALE",
    "T600_NO_VALID_MID",
    "LUNCH_CROSS",
    "CAPTURE_GAP",
    "SYMBOL_DATA_MISSING",
    "ENDPOINT_LOOKUP_TOLERANCE",
    "STALE_SESSION_CONSTANT",
    "OTHER",
)


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def last_idx(t: np.ndarray, t_at: float) -> int:
    if t is None or t.size == 0:
        return -1
    return int(np.searchsorted(t, float(t_at), side="right") - 1)


def probe_last(board: dict[str, np.ndarray], t_at: float) -> dict[str, Any]:
    t = board.get("t")
    empty = {
        "has_event": False,
        "i": -1,
        "lag_sec": None,
        "executable": False,
        "special": False,
        "bid": None,
        "ask": None,
        "bid_qty": None,
        "ask_qty": None,
        "fresh_sec": None,
        "state": None,
        "locked": False,
        "qty_ok": False,
        "n_after": int(t.size) if t is not None else 0,
    }
    if t is None or t.size == 0:
        empty["n_after"] = 0
        return empty
    i = last_idx(t, t_at)
    n_after = int(np.sum(t > float(t_at) + 1e-12))
    if i < 0:
        empty["n_after"] = n_after
        return empty
    bid = _f(board["bid"][i]) if i < board["bid"].size else None
    ask = _f(board["ask"][i]) if i < board["ask"].size else None
    bq = _f(board["bid_qty"][i]) if i < board["bid_qty"].size else None
    aq = _f(board["ask_qty"][i]) if i < board["ask_qty"].size else None
    fresh = _f(board["fresh_sec"][i]) if i < board["fresh_sec"].size else None
    exe_arr = board.get("executable")
    spec_arr = board.get("special")
    state_arr = board.get("board_execution_state")
    exe = bool(exe_arr[i]) if exe_arr is not None and i < int(exe_arr.size) else False
    special = bool(spec_arr[i]) if spec_arr is not None and i < int(spec_arr.size) else False
    st = ""
    if state_arr is not None and i < int(getattr(state_arr, "size", 0) or 0):
        st = str(state_arr[i] or "")
    locked = bid is not None and ask is not None and bid > 0 and ask > 0 and bid + 1e-12 >= ask
    qty_ok = (bq is not None and aq is not None and bq >= float(MIN_QTY) - 1e-12 and aq >= float(MIN_QTY) - 1e-12)
    return {
        "has_event": True,
        "i": i,
        "lag_sec": float(t_at) - float(t[i]),
        "executable": exe,
        "special": special,
        "bid": bid,
        "ask": ask,
        "bid_qty": bq,
        "ask_qty": aq,
        "fresh_sec": fresh,
        "state": st or None,
        "locked": locked,
        "qty_ok": qty_ok,
        "n_after": n_after,
    }


def n_events_between(board: dict[str, np.ndarray], t_lo: float, t_hi: float) -> int:
    t = board.get("t")
    if t is None or t.size == 0:
        return 0
    return int(np.sum((t > float(t_lo) + 1e-12) & (t <= float(t_hi) + 1e-12)))


def n_events_window(board: dict[str, np.ndarray], t_lo: float, t_hi: float) -> int:
    t = board.get("t")
    if t is None or t.size == 0:
        return 0
    return int(np.sum((t >= float(t_lo) - 1e-12) & (t <= float(t_hi) + 1e-12)))


def tse_phase_from_hm(h: int, m: int) -> str:
    return tse_phase_hm(int(h), int(m))


def first_fail_reason(
    *,
    board_empty: bool,
    t0: dict[str, Any],
    t1: dict[str, Any],
    t0_hm: tuple[int, int],
    t1_hm: tuple[int, int],
    t0m: Optional[dict[str, Any]],
    t1m: Optional[dict[str, Any]],
    v1r_endpoint_in_session: bool,
    n_t600_events: int,
    primary_valid: bool,
) -> Optional[str]:
    """One canonical reason per PRIMARY-null row. None if PRIMARY valid.

    Order is first-fail, not a bag of flags. STALE_SESSION_CONSTANT is used only
    when t+600 is still TSE Zaraba but V2 treated PM 15:00 as continuous end.
    """
    if primary_valid:
        return None
    h0, m0 = t0_hm
    h1, m1 = t1_hm
    p0 = tse_phase_hm(h0, m0)
    p1 = tse_phase_hm(h1, m1)
    t0_cont = tse_continuous_at_hm(h0, m0)
    t1_cont = tse_continuous_at_hm(h1, m1)
    t1_auction = p1 in {"PM_CLOSING_AUCTION", "PM_MARKET_CLOSE"}

    if board_empty:
        return "SYMBOL_DATA_MISSING"
    if not t0.get("has_event"):
        return "CAPTURE_GAP" if int(t0.get("n_after") or 0) > 0 else "SYMBOL_DATA_MISSING"

    lag0 = t0.get("lag_sec")
    if lag0 is not None and float(lag0) > float(CAPTURE_GAP_LAG_SEC) + 1e-12:
        return "CAPTURE_GAP"
    if not t0_cont:
        return "T0_OUTSIDE_SESSION"
    if not bool(t0.get("executable")):
        return "T0_NON_EXECUTABLE"
    if lag0 is not None and float(lag0) > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return "T0_STALE"
    if t0m is None:
        return "T0_NO_VALID_MID"

    if p0 == "AM_CONTINUOUS" and p1 == "LUNCH":
        return "LUNCH_CROSS"
    if t1_auction:
        return "T600_CLOSING_AUCTION"
    if not t1_cont:
        return "T600_OUTSIDE_CONTINUOUS"
    if not v1r_endpoint_in_session and t1_cont:
        return "STALE_SESSION_CONSTANT"
    if int(n_t600_events) <= 0:
        return "T600_NO_EVENT"
    lag1 = t1.get("lag_sec")
    if not bool(t1.get("has_event")):
        return "T600_NO_EVENT"
    if not bool(t1.get("executable")):
        return "T600_NON_EXECUTABLE"
    if lag1 is not None and float(lag1) > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return "T600_STALE"
    if t1m is None:
        return "T600_NO_VALID_MID"
    return "OTHER"


def mid_pack(hit: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not hit:
        return {"ok": False, "mid": None, "lag_sec": None, "event_t": None}
    return {
        "ok": True,
        "mid": hit.get("mid"),
        "lag_sec": hit.get("lag_sec"),
        "event_t": hit.get("event_t"),
    }


def lookup_t0_t1(
    board: dict[str, np.ndarray],
    t0: float,
    t1: float,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    t0m = last_executable_mid(board, t0)
    t1m = last_executable_mid(board, t1, t_lo=t0)
    return t0m, t1m

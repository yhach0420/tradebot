"""Executable-only t0 / t+600 mid contract. No itayose / locked / special fallback."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.executable_target_v2_b_threshold import HORIZON_SEC, PRIMARY_TARGET

ITAYOSE_LIKE = frozenset(
    {
        "ITAYOSE_LOCKED_OR_CROSSED",
        "PREOPEN_INDICATIVE",
        "SPECIAL_QUOTE",
        "NON_EXECUTABLE",
        "STALE_OR_MISSING",
    }
)


def session_of_anchor(h: int) -> str:
    return "AM" if int(h) < 12 else "PM"


def continuous_session_end(day: str, session: str) -> float:
    """V1R canonical continuous close: AM 11:30 / PM 15:00. Not PBv2 15:23/15:30."""
    return float(session_end_epoch(day, session))


def last_executable_mid(
    board: dict[str, np.ndarray],
    t_at: float,
    *,
    t_lo: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Last valid executable continuous mid at/before t_at, not stale vs t_at.

    Forbidden: itayose/locked/special/non-executable/synthetic. No walk-back
    past BOARD_FRESHNESS_SEC — that would be last-stale-board fallback.
    """
    t = board.get("t")
    if t is None or t.size == 0:
        return None
    i = int(np.searchsorted(t, float(t_at), side="right") - 1)
    if i < 0:
        return None
    exe = board.get("executable")
    special = board.get("special")
    state = board.get("board_execution_state")
    for j in range(i, -1, -1):
        tj = float(t[j])
        if t_lo is not None and tj + 1e-12 < float(t_lo):
            break
        if float(t_at) - tj > float(BOARD_FRESHNESS_SEC) + 1e-12:
            break
        if exe is not None and j < int(exe.size) and not bool(exe[j]):
            continue
        if special is not None and j < int(special.size) and bool(special[j]):
            continue
        fresh = float(board["fresh_sec"][j]) if np.isfinite(board["fresh_sec"][j]) else 0.0
        if fresh > float(BOARD_FRESHNESS_SEC) + 1e-12:
            continue
        bid = float(board["bid"][j])
        ask = float(board["ask"][j])
        if not (np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask > 0):
            continue
        if bid + 1e-12 >= ask:
            continue
        bq = float(board["bid_qty"][j]) if np.isfinite(board["bid_qty"][j]) else 0.0
        aq = float(board["ask_qty"][j]) if np.isfinite(board["ask_qty"][j]) else 0.0
        if bq < float(MIN_QTY) - 1e-12 or aq < float(MIN_QTY) - 1e-12:
            continue
        st = str(state[j] or "") if state is not None and j < int(getattr(state, "size", 0) or 0) else ""
        mid = (bid + ask) / 2.0
        return {
            "mid": float(mid),
            "bid": float(bid),
            "ask": float(ask),
            "event_t": tj,
            "lag_sec": float(t_at) - tj,
            "fresh_sec": fresh,
            "state": st or "CONTINUOUS_TRADING",
            "executable": True,
        }
    return None


def classify_t0_source(board: dict[str, np.ndarray], t0: float, exe0: bool) -> str:
    t = board.get("t")
    if t is None or t.size == 0:
        return "STALE_OR_MISSING"
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return "STALE_OR_MISSING"
    bid = float(board["bid"][i]) if np.isfinite(board["bid"][i]) else None
    ask = float(board["ask"][i]) if np.isfinite(board["ask"][i]) else None
    st = ""
    state = board.get("board_execution_state")
    if state is not None and i < int(getattr(state, "size", 0) or 0):
        st = str(state[i] or "")
    locked = bid is not None and ask is not None and bid > 0 and ask > 0 and bid + 1e-12 >= ask
    if exe0 and not locked:
        return "EXECUTABLE_CONTINUOUS_MID"
    if locked:
        return "ITAYOSE_LOCKED_OR_CROSSED"
    if st in {"NOT_OPENED", "PREOPEN_ITAYOSE"}:
        return "PREOPEN_INDICATIVE"
    if "SPECIAL" in st:
        return "SPECIAL_QUOTE"
    if not exe0:
        return "NON_EXECUTABLE"
    return "STALE_OR_MISSING"


def primary_target_row(
    board: dict[str, np.ndarray],
    *,
    day: str,
    symbol: str,
    anchor: str,
    session: str,
    t0: float,
) -> dict[str, Any]:
    sess_end = continuous_session_end(day, session)
    t1 = float(t0) + float(HORIZON_SEC)
    exe_arr = board.get("executable")
    i0 = int(np.searchsorted(board["t"], float(t0), side="right") - 1) if board["t"].size else -1
    exe0 = bool(exe_arr[i0]) if exe_arr is not None and i0 >= 0 and i0 < int(exe_arr.size) else False
    src0 = classify_t0_source(board, t0, exe0)
    t0m = last_executable_mid(board, t0)
    endpoint_in_session = t1 <= sess_end + 1e-12
    t1m = last_executable_mid(board, t1, t_lo=t0) if endpoint_in_session else None
    sess_close_mid = last_executable_mid(board, sess_end, t_lo=t0) if float(t0) < sess_end - 1e-12 else None
    target = None
    null_reason = None
    if not exe0:
        null_reason = "NON_EXECUTABLE_T0"
    elif t0m is None:
        null_reason = "NO_VALID_EXECUTABLE_T0_MID"
    elif not endpoint_in_session:
        null_reason = "ENDPOINT_OUTSIDE_CONTINUOUS_SESSION"
    elif t1m is None:
        null_reason = "NO_VALID_EXECUTABLE_ENDPOINT_MID"
    else:
        target = float(t1m["mid"] / t0m["mid"] - 1.0)

    model_eligible = bool(exe0 and t0m is not None and target is not None)
    is_1520 = str(anchor) == "15:20"
    status_1520 = None
    if is_1520:
        status_1520 = "EXCLUDED_NONCONTINUOUS_ENDPOINT" if not endpoint_in_session else (
            "VALID" if target is not None else "EXCLUDED_NO_VALID_ENDPOINT_MID"
        )
    sess_close_ret = None
    if t0m is not None and sess_close_mid is not None:
        sess_close_ret = float(sess_close_mid["mid"] / t0m["mid"] - 1.0)
    return {
        "date": day,
        "symbol": symbol,
        "anchor": anchor,
        "session": session,
        "executable_at_t0": exe0,
        "t0_price_source": src0,
        "itayose_like_t0": src0 in ITAYOSE_LIKE,
        "MODEL_ROW_ELIGIBLE": model_eligible,
        PRIMARY_TARGET: target,
        "SESSION_CLOSE_RETURN": sess_close_ret,
        "PRIMARY_TARGET_NULL": target is None,
        "null_reason": null_reason,
        "t0_mid": None if t0m is None else t0m["mid"],
        "t0_bid": None if t0m is None else t0m["bid"],
        "t0_ask": None if t0m is None else t0m["ask"],
        "t0_lag_sec": None if t0m is None else t0m["lag_sec"],
        "mid_600": None if t1m is None else t1m["mid"],
        "endpoint_in_session": endpoint_in_session,
        "session_end": sess_end,
        "target_time": t1,
        "is_15_20": is_1520,
        "status_15_20": status_1520,
        "ITAYOSE_BASE": bool(model_eligible and src0 in ITAYOSE_LIKE),
        "NON_EXECUTABLE_T0_IN_PRIMARY": bool(model_eligible and not exe0),
        "OPENS_WITHIN_1S_USED": False,
    }

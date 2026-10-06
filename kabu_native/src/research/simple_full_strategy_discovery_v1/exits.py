"""Five frozen Technical EXIT rules plus operational session close. First causal Bid1."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.new_entry_breakout_continuation_v1.harvest import CONTINUOUS_STATES
from replay.pnl_yen import compute_pnl_yen_100
from research.simple_full_strategy_discovery_v1 import BOARD_FRESHNESS_SEC, MIN_QTY, SHARES


def _fin(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    return v if v == v else None


def _fresh_ok(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x and x <= float(BOARD_FRESHNESS_SEC) + 1e-12


def _bid_ok(board: dict[str, np.ndarray], i: int) -> bool:
    if not bool(board["executable"][i]) or not bool(board["continuous"][i]):
        return False
    if bool(board["special"][i]):
        return False
    if str(board["board_execution_state"][i] or "") not in CONTINUOUS_STATES:
        return False
    if not _fresh_ok(board["bid_fresh_sec"][i]):
        return False
    bid = float(board["bid"][i])
    bq = float(board["bid_qty"][i])
    if not (bid == bid) or bid <= 0:
        return False
    if not (bq == bq) or bq < float(MIN_QTY) - 1e-12:
        return False
    return True


def first_causal_bid(board: dict[str, np.ndarray], *, event_t: float, sess_end: float) -> dict[str, Any]:
    t = board.get("t")
    out = {"exit_t": None, "exit_bid": None, "miss": True}
    if t is None or int(t.size) == 0:
        return out
    i0 = int(np.searchsorted(t, float(event_t), side="left"))
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti + 1e-12 < float(event_t):
            continue
        if ti > float(sess_end) + 1e-12:
            break
        if not _bid_ok(board, i):
            continue
        return {"exit_t": ti, "exit_bid": float(board["bid"][i]), "miss": False}
    return out


def last_session_bid(board: dict[str, np.ndarray], *, fill_t: float, sess_end: float) -> dict[str, Any]:
    t = board.get("t")
    out = {"exit_t": None, "exit_bid": None, "miss": True}
    if t is None or int(t.size) == 0:
        return out
    i_hi = int(np.searchsorted(t, float(sess_end), side="right") - 1)
    for i in range(i_hi, -1, -1):
        ti = float(t[i])
        if ti > float(sess_end) + 1e-12:
            continue
        if ti + 1e-12 < float(fill_t):
            break
        if not _bid_ok(board, i):
            continue
        return {"exit_t": ti, "exit_bid": float(board["bid"][i]), "miss": False}
    return out


def path_mfe_mae_yen(board: dict[str, np.ndarray], *, fill_t: float, fill_px: float, path_end: float) -> tuple[Optional[float], Optional[float]]:
    t = board.get("t")
    if t is None or int(t.size) == 0 or fill_px is None or float(fill_px) <= 0:
        return None, None
    i0 = int(np.searchsorted(t, float(fill_t), side="right"))
    mfe = None
    mae = None
    for i in range(i0, int(t.size)):
        ti = float(t[i])
        if ti <= float(fill_t) + 1e-12:
            continue
        if ti > float(path_end) + 1e-12:
            break
        if not _bid_ok(board, i):
            continue
        yen = (float(board["bid"][i]) - float(fill_px)) * float(SHARES)
        if mfe is None or yen > mfe:
            mfe = yen
        if mae is None or yen < mae:
            mae = yen
    return mfe, mae


def _post_fill_bars(finalize: np.ndarray, fill_t: float) -> list[int]:
    out = []
    for i in range(int(finalize.size)):
        if float(finalize[i]) > float(fill_t) + 1e-12:
            out.append(i)
    return out


def technical_fire_i(
    exit_id: str,
    *,
    idxs: list[int],
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    vwap: np.ndarray,
) -> Optional[int]:
    if not idxs:
        return None
    if exit_id == "Z1":
        for i in idxs:
            c = _fin(close, i)
            vw = _fin(vwap, i)
            if c is not None and vw is not None and c < vw:
                return i
        return None
    if exit_id == "Z2":
        for i in idxs:
            c = _fin(close, i)
            lo = _fin(low, i - 1)
            if c is not None and lo is not None and c < lo:
                return i
        return None
    if exit_id == "Z3":
        prev_weak = False
        for i in idxs:
            c = _fin(close, i)
            op = _fin(open_, i)
            c1 = _fin(close, i - 1)
            weak = c is not None and op is not None and c1 is not None and c < op and c < c1
            if weak and prev_weak:
                return i
            prev_weak = bool(weak)
        return None
    if exit_id == "Z4":
        peak = None
        for i in idxs:
            hi = _fin(high, i)
            if hi is not None:
                peak = hi if peak is None else max(peak, hi)
            if peak is None:
                continue
            c = _fin(close, i)
            lo = _fin(low, i - 1)
            if c is not None and lo is not None and c < lo:
                return i
        return None
    if exit_id == "Z5":
        a = technical_fire_i("Z1", idxs=idxs, open_=open_, high=high, low=low, close=close, vwap=vwap)
        b = technical_fire_i("Z2", idxs=idxs, open_=open_, high=high, low=low, close=close, vwap=vwap)
        cands = [x for x in (a, b) if x is not None]
        return min(cands) if cands else None
    return None


def resolve_exit(
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    *,
    exit_id: str,
    fill_t: float,
    fill_px: float,
    sess_end: float,
) -> dict[str, Any]:
    idxs = _post_fill_bars(raw["finalize_t"], float(fill_t))
    fire_i = technical_fire_i(
        exit_id,
        idxs=idxs,
        open_=raw["open"],
        high=raw["high"],
        low=raw["low"],
        close=raw["close"],
        vwap=vwap,
    )
    sess = last_session_bid(board, fill_t=float(fill_t), sess_end=float(sess_end))
    tech = {"exit_t": None, "exit_bid": None, "miss": True, "reason": "NO_TECH"}
    if fire_i is not None:
        trig_t = float(raw["finalize_t"][fire_i])
        got = first_causal_bid(board, event_t=trig_t, sess_end=float(sess_end))
        tech = {**got, "reason": exit_id, "trigger_t": trig_t, "trigger_i": fire_i}
    chosen = tech
    reason = str(tech.get("reason") or exit_id)
    if tech.get("miss") or tech.get("exit_t") is None:
        chosen = {**sess, "reason": "SESSION_CLOSE"}
        reason = "SESSION_CLOSE"
    elif sess.get("exit_t") is not None and float(sess["exit_t"]) + 1e-12 < float(tech["exit_t"]):
        chosen = {**sess, "reason": "SESSION_CLOSE"}
        reason = "SESSION_CLOSE"
    if chosen.get("miss") or chosen.get("exit_t") is None or chosen.get("exit_bid") is None:
        return {
            "exit_t": None,
            "exit_price": None,
            "exit_reason": "EXIT_MISS",
            "pnl_yen_100": None,
            "miss": True,
            "hold_sec": None,
            "mfe_yen": None,
            "mae_yen": None,
            "profit_giveback": None,
            "loss_avoided": None,
        }
    exit_t = float(chosen["exit_t"])
    exit_px = float(chosen["exit_bid"])
    pnl = float(compute_pnl_yen_100(float(fill_px), exit_px, side="long"))
    mfe, mae = path_mfe_mae_yen(board, fill_t=float(fill_t), fill_px=float(fill_px), path_end=exit_t)
    giveback = None if mfe is None else max(0.0, float(mfe) - float(pnl))
    avoided = None if mae is None else max(0.0, abs(float(mae)) - max(0.0, -float(pnl)))
    return {
        "exit_t": exit_t,
        "exit_price": exit_px,
        "exit_reason": reason,
        "pnl_yen_100": pnl,
        "miss": False,
        "hold_sec": float(exit_t - float(fill_t)),
        "mfe_yen": mfe,
        "mae_yen": mae,
        "profit_giveback": giveback,
        "loss_avoided": avoided,
        "trigger_t": tech.get("trigger_t"),
    }

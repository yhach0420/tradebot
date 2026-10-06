"""Operator technical EXIT plus 11:29 session flatten. First causal Bid1. No walkback on 42."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from replay.pnl_yen import compute_pnl_yen_100
from research.full_causal_mechanism_discovery_v1 import EXIT_O1, EXIT_O2, EXIT_O3
from research.simple_full_strategy_discovery_v1.exits import first_causal_bid, path_mfe_mae_yen, resolve_exit


def _fin(arr: np.ndarray, i: int) -> float | None:
    if i < 0 or i >= int(arr.size):
        return None
    v = float(arr[i])
    return v if v == v else None


def post_fill_bars(finalize: np.ndarray, fill_t: float) -> list[int]:
    out = []
    for i in range(int(finalize.size)):
        if float(finalize[i]) > float(fill_t) + 1e-12:
            out.append(i)
    return out


def o1_fire_i(idxs: list[int], p: np.ndarray) -> Optional[int]:
    for i in idxs:
        if not bool(p[i]):
            return int(i)
    return None


def o2_fire_i(idxs: list[int], p: np.ndarray, q: np.ndarray) -> Optional[int]:
    for i in idxs:
        if not (bool(p[i]) and bool(q[i])):
            return int(i)
    return None


def o3_fire_i(idxs: list[int], close: np.ndarray, thesis_level: float) -> Optional[int]:
    lvl = float(thesis_level)
    for i in idxs:
        c = _fin(close, i)
        if c is not None and float(c) < lvl:
            return int(i)
    return None


def freeze_thesis_level(vwap: np.ndarray, signal_i: int) -> Optional[float]:
    return _fin(vwap, int(signal_i))


def _pack_fill(
    *,
    fill_t: float,
    fill_px: float,
    chosen: dict[str, Any],
    reason: str,
    tech: dict[str, Any],
    board: dict[str, np.ndarray],
) -> dict[str, Any]:
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
            "trigger_t": tech.get("trigger_t"),
            "trigger_i": tech.get("trigger_i"),
            "session_walkback": False,
        }
    exit_t = float(chosen["exit_t"])
    exit_px = float(chosen["exit_bid"])
    pnl = float(compute_pnl_yen_100(float(fill_px), exit_px, side="long"))
    mfe, mae = path_mfe_mae_yen(board, fill_t=float(fill_t), fill_px=float(fill_px), path_end=exit_t)
    return {
        "exit_t": exit_t,
        "exit_price": exit_px,
        "exit_reason": reason,
        "pnl_yen_100": pnl,
        "miss": False,
        "hold_sec": float(exit_t - float(fill_t)),
        "mfe_yen": mfe,
        "mae_yen": mae,
        "trigger_t": tech.get("trigger_t"),
        "trigger_i": tech.get("trigger_i"),
        "session_walkback": False,
    }


def resolve_operator_exit(
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    *,
    exit_id: str,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    flatten_t: float,
    fire_i: Optional[int],
) -> dict[str, Any]:
    sess_event = max(float(fill_t), float(flatten_t))
    sess = first_causal_bid(board, event_t=float(sess_event), sess_end=float(sess_end))
    tech = {"exit_t": None, "exit_bid": None, "miss": True, "reason": "NO_TECH", "trigger_t": None, "trigger_i": None}
    trig_t = None
    if fire_i is not None:
        trig_t = float(raw["finalize_t"][int(fire_i)])
        got = first_causal_bid(board, event_t=float(trig_t), sess_end=float(sess_end))
        tech = {**got, "reason": exit_id, "trigger_t": trig_t, "trigger_i": int(fire_i)}
    tech_eligible = trig_t is not None and float(trig_t) <= float(flatten_t) + 1e-12
    if tech_eligible and not tech.get("miss") and tech.get("exit_t") is not None:
        if sess.get("exit_t") is not None and float(sess["exit_t"]) + 1e-12 < float(tech["exit_t"]):
            return _pack_fill(
                fill_t=fill_t, fill_px=fill_px, chosen=sess, reason="SESSION_CLOSE", tech=tech, board=board
            )
        return _pack_fill(fill_t=fill_t, fill_px=fill_px, chosen=tech, reason=exit_id, tech=tech, board=board)
    return _pack_fill(fill_t=fill_t, fill_px=fill_px, chosen=sess, reason="SESSION_CLOSE", tech=tech, board=board)


def resolve_canary_exit(
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
) -> dict[str, Any]:
    got = resolve_exit(
        board,
        raw,
        vwap,
        exit_id="Z3",
        fill_t=float(fill_t),
        fill_px=float(fill_px),
        sess_end=float(sess_end),
    )
    got["session_walkback"] = True
    return got


def fire_for_operator(
    *,
    operator: str,
    idxs: list[int],
    series: dict[str, np.ndarray],
    predicates: list[str],
    close: np.ndarray,
    thesis_level: Optional[float],
) -> Optional[int]:
    if operator == "O1_PERSIST_NEXT":
        return o1_fire_i(idxs, series[str(predicates[0])])
    if operator == "O2_HANDOFF_NEXT":
        return o2_fire_i(idxs, series[str(predicates[0])], series[str(predicates[1])])
    if operator == "O3_RECLAIM_ACCEPT_NEXT":
        if thesis_level is None:
            return None
        return o3_fire_i(idxs, close, float(thesis_level))
    raise ValueError(operator)


assert EXIT_O1 and EXIT_O2 and EXIT_O3

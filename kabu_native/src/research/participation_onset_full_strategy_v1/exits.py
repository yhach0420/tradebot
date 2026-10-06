"""Z3 price weakness, ZP participation loss, ZH first-fire. EXIT_PENDING until first Bid1."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.participation_onset_full_strategy_v1 import VOLUME_PERCENTILE_THRESHOLD
from research.simple_full_strategy_discovery_v1.exits import (
    first_causal_bid,
    last_session_bid,
    path_mfe_mae_yen,
    technical_fire_i,
)
from replay.pnl_yen import compute_pnl_yen_100


def zp_trigger_t(grid: list[dict[str, Any]], *, fill_t: float) -> Optional[float]:
    """First post-fill evaluable 10s grid with volume_percentile_60s < frozen T1 threshold.

    Missing/not-evaluable does not fire.
    """
    for row in grid:
        t = float(row["grid_epoch"])
        if t <= float(fill_t) + 1e-12:
            continue
        if not bool(row.get("evaluable")):
            continue
        v = row.get("volume_percentile_60s")
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x != x:
            continue
        if x < float(VOLUME_PERCENTILE_THRESHOLD):
            return t
    return None


def _pack(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_px: float,
    sess_end: float,
    tech_t: Optional[float],
    tech_reason: str,
) -> dict[str, Any]:
    sess = last_session_bid(board, fill_t=float(fill_t), sess_end=float(sess_end))
    tech = {"exit_t": None, "exit_bid": None, "miss": True, "reason": "NO_TECH", "trigger_t": tech_t}
    if tech_t is not None:
        got = first_causal_bid(board, event_t=float(tech_t), sess_end=float(sess_end))
        tech = {**got, "reason": tech_reason, "trigger_t": float(tech_t)}
    chosen = tech
    reason = str(tech.get("reason") or tech_reason)
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
            "trigger_t": tech_t,
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


def z3_trigger_t(raw: dict[str, np.ndarray], vwap: np.ndarray, *, fill_t: float) -> Optional[float]:
    idxs = []
    finalize = raw["finalize_t"]
    for i in range(int(finalize.size)):
        if float(finalize[i]) > float(fill_t) + 1e-12:
            idxs.append(i)
    fire_i = technical_fire_i(
        "Z3",
        idxs=idxs,
        open_=raw["open"],
        high=raw["high"],
        low=raw["low"],
        close=raw["close"],
        vwap=vwap,
    )
    if fire_i is None:
        return None
    return float(raw["finalize_t"][fire_i])


def resolve_exit(
    board: dict[str, np.ndarray],
    raw: dict[str, np.ndarray],
    vwap: np.ndarray,
    grid: list[dict[str, Any]],
    *,
    exit_id: str,
    fill_t: float,
    fill_px: float,
    sess_end: float,
) -> dict[str, Any]:
    z3_t = z3_trigger_t(raw, vwap, fill_t=float(fill_t)) if exit_id in ("Z3", "ZH") else None
    zp_t = zp_trigger_t(grid, fill_t=float(fill_t)) if exit_id in ("ZP", "ZH") else None
    tech_t: Optional[float] = None
    tech_reason = exit_id
    if exit_id == "Z3":
        tech_t = z3_t
        tech_reason = "Z3"
    elif exit_id == "ZP":
        tech_t = zp_t
        tech_reason = "ZP"
    elif exit_id == "ZH":
        cands = [(t, r) for t, r in ((z3_t, "Z3"), (zp_t, "ZP")) if t is not None]
        if cands:
            tech_t, tech_reason = min(cands, key=lambda x: float(x[0]))
        else:
            tech_t = None
            tech_reason = "ZH"
    else:
        return {
            "exit_t": None,
            "exit_price": None,
            "exit_reason": "EXIT_MISS",
            "pnl_yen_100": None,
            "miss": True,
        }
    return _pack(board, fill_t=float(fill_t), fill_px=float(fill_px), sess_end=float(sess_end), tech_t=tech_t, tech_reason=tech_reason)

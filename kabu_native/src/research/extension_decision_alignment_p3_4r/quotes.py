"""Causal quotes at an absolute event_time. last valid t <= asof. No future-nearest."""
from __future__ import annotations

from typing import Any

from research.fixed_anchor_mechanism_audit_p3_0.grid import AM_END, PM_END, hm_epoch, session_of_epoch
from research.post_fill_edge_decomposition_p3_2.quotes import last_valid_side, mid_at_or_before


def asof_session_ok(day: str, session: str, asof: float) -> bool:
    end = hm_epoch(day, *(AM_END if session == "AM" else PM_END))
    if float(asof) > float(end) + 1e-12:
        return False
    sess = session_of_epoch(day, float(asof))
    return sess is not None and sess == session


def quotes_asof(board: dict, *, day: str, session: str, asof: float) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "status": "OK",
        "asof": float(asof),
        "bid1": None,
        "bid1_t": None,
        "mid": None,
        "leak_n": 0,
        "evaluable": False,
    }
    if not asof_session_ok(day, session, float(asof)):
        rec["status"] = "NOT_EVALUABLE"
        return rec
    bid, bt, leak_b = last_valid_side(board, float(asof), side="bid")
    mid = mid_at_or_before(board, float(asof))
    rec["bid1"] = bid
    rec["bid1_t"] = bt
    rec["mid"] = mid.get("mid")
    rec["leak_n"] = int(leak_b) + int(mid.get("leak_n") or 0)
    rec["evaluable"] = bid is not None and rec["leak_n"] == 0
    rec["mid_evaluable"] = bool(mid.get("evaluable")) and int(mid.get("leak_n") or 0) == 0
    if rec["leak_n"]:
        rec["evaluable"] = False
        rec["mid_evaluable"] = False
    return rec

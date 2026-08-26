"""Causal 600s extension decision from Runtime functions. Board ticks t <= t600 only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x35_passive_exit.paths import build_path
from research.fixed_selection_edge_decomposition_p3_1.scan import horizon_status
from research.post_fill_edge_decomposition_p3_2.quotes import last_valid_side, mid_at_or_before
from research.v1r_exit_v2_asymmetric.continuation import continuation_supported, features_at_600
from research.v1r_exit_v2_asymmetric.guards import detect_guard_trigger
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from small_paper.v1r_exit_v2_contract import FROZEN_CONTINUATION, FROZEN_GUARD
from small_paper.v1r_live_dual_lane import session_end_for_position

from research.fixed_winner_cluster_extension_p3_4 import IDENTITY_REL_TOL


def slice_board_le(board: dict[str, np.ndarray], until: float) -> tuple[dict[str, np.ndarray], int]:
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0:
        return board, 0
    n = int(np.searchsorted(t, float(until), side="right"))
    leak = 0
    if n > 0 and float(t[n - 1]) > float(until) + 1e-12:
        leak += 1
        while n > 0 and float(t[n - 1]) > float(until) + 1e-12:
            n -= 1
            leak += 1
    out = {}
    for k, v in board.items():
        if hasattr(v, "__getitem__") and getattr(v, "size", None) is not None:
            out[k] = v[:n]
        else:
            out[k] = v
    return out, leak


def reconstruct_600_decision(
    board: dict[str, np.ndarray],
    *,
    date: str,
    symbol: str,
    session: str,
    fill_time: float,
    fill_price: float,
) -> dict[str, Any]:
    """Rebuild Arch E 600s gate using only event_time <= fill+600."""
    t600 = float(fill_time) + 600.0
    sliced, leak = slice_board_le(board, t600)
    sess_end = session_end_for_position(date=date, session=session, fill_time=float(fill_time))
    path = build_path(
        sliced,
        entry_price=float(fill_price),
        entry_t=float(fill_time),
        sess_end=min(float(sess_end), t600),
    )
    out: dict[str, Any] = {
        "t600": t600,
        "decision_future_leak": int(leak),
        "path_ok": bool(path.get("ok")),
        "guard_hit": False,
        "extended": False,
        "recon_class": None,
        "recon_reason": None,
        "feat_ret": None,
        "feat_mfe": None,
        "feat_imb": None,
        "feat_gb_frac": None,
        "continuation_id": FROZEN_CONTINUATION.get("id"),
        "guard_id": FROZEN_GUARD.get("id"),
    }
    if not path.get("ok"):
        out["recon_class"] = "NOT_EVALUABLE"
        out["recon_reason"] = "PATH_NOT_OK"
        return out
    fill = {
        "date": date,
        "symbol": symbol,
        "session": session,
        "fill_time": float(fill_time),
        "fill_price": float(fill_price),
        "anchor_id": "P3_4_T600",
    }
    bundle = build_trade_bundle(fill, path, sliced)
    ghit = detect_guard_trigger(bundle, FROZEN_GUARD)
    if ghit.get("hit"):
        out["guard_hit"] = True
        out["recon_class"] = "GUARD_EXIT"
        out["recon_reason"] = ghit.get("reason") or "IMBALANCE"
        return out
    feats = features_at_600(bundle)
    out["feat_ret"] = feats.get("ret")
    out["feat_mfe"] = feats.get("mfe")
    out["feat_imb"] = feats.get("imb")
    out["feat_gb_frac"] = feats.get("gb_frac")
    extend = bool(continuation_supported(bundle, FROZEN_CONTINUATION))
    out["extended"] = extend
    out["recon_class"] = "EXTEND_TO_750" if extend else "EXIT_AT_600"
    out["recon_reason"] = "CONT_EXTEND_750" if extend else "CONT_EXIT_600"
    return out


def checkpoint_quotes(
    board: dict[str, np.ndarray],
    *,
    day: str,
    session: str,
    fill_time: float,
    horizon_sec: int,
) -> dict[str, Any]:
    chk = float(fill_time) + float(horizon_sec)
    st = horizon_status(day, session, float(fill_time), int(horizon_sec))
    rec: dict[str, Any] = {
        "status": st,
        "checkpoint": chk,
        "bid1": None,
        "bid1_t": None,
        "mid": None,
        "leak_n": 0,
        "evaluable": False,
    }
    if st != "OK":
        rec["status"] = "NOT_EVALUABLE"
        return rec
    bid, bt, leak_b = last_valid_side(board, chk, side="bid")
    mid = mid_at_or_before(board, chk)
    rec["bid1"] = bid
    rec["bid1_t"] = bt
    rec["mid"] = mid.get("mid")
    rec["leak_n"] = int(leak_b) + int(mid.get("leak_n") or 0)
    rec["evaluable"] = bid is not None
    rec["mid_evaluable"] = bool(mid.get("evaluable"))
    if rec["leak_n"]:
        rec["evaluable"] = False
    return rec


def rel_close(a: float, b: float) -> bool:
    denom = max(abs(a), abs(b), 1e-12)
    return abs(float(a) - float(b)) / denom <= IDENTITY_REL_TOL or abs(float(a) - float(b)) <= 1e-12

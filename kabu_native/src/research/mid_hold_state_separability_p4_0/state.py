"""Causal mid-hold state at a research observation checkpoint. No EXIT."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x35_passive_exit.paths import _valid_bid, build_path
from research.extension_decision_alignment_p3_4r.quotes import quotes_asof
from research.fixed_winner_cluster_extension_p3_4.decision import rel_close, slice_board_le
from research.mid_hold_state_separability_p4_0 import FROZEN_GUARD, IDENTITY_REL_TOL
from research.post_fill_edge_decomposition_p3_2.quotes import last_valid_side, mid_at_or_before
from research.v1r_exit_global_search.apply_exit import attach_board_series
from research.v1r_exit_v2_asymmetric.states import state_at_off
from small_paper.v1r_live_dual_lane import session_end_for_position


def _bid_extrema(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    until: float,
) -> tuple[Optional[float], Optional[float], int]:
    t = board.get("t")
    if t is None or getattr(t, "size", 0) == 0:
        return None, None, 0
    i0 = int(np.searchsorted(t, float(fill_t), side="left"))
    i1 = int(np.searchsorted(t, float(until), side="right"))
    mx = mn = None
    n = 0
    leak = 0
    for i in range(i0, i1):
        ti = float(t[i])
        if ti + 1e-12 < float(fill_t):
            continue
        if ti > float(until) + 1e-12:
            leak += 1
            continue
        if not _valid_bid(board, i):
            continue
        b = float(board["bid"][i])
        n += 1
        mx = b if mx is None or b > mx else mx
        mn = b if mn is None or b < mn else mn
    return mx, mn, leak


def imb_p5_persist_at_end(path: dict[str, Any], *, persist_sec: float, thr: float) -> Optional[bool]:
    """Existing IMB_p5_t-10 persist formula on the last persist_sec of a sliced path. Not an EXIT."""
    imb = path.get("imb")
    offs = path.get("offs")
    if imb is None or offs is None or getattr(offs, "size", 0) == 0:
        return None
    o = float(offs[-1])
    if o + 1e-12 < persist_sec:
        return False
    k0 = int(np.searchsorted(offs, o - persist_sec, side="left"))
    sl = imb[k0:]
    if sl.size == 0 or not np.all(np.isfinite(sl)):
        return False
    return bool(np.all(sl <= float(thr) + 1e-12))


def checkpoint_state(
    board: dict[str, np.ndarray],
    *,
    day: str,
    session: str,
    fill_time: float,
    fill_price: float,
    horizon_sec: int,
) -> dict[str, Any]:
    t_abs = float(fill_time) + float(horizon_sec)
    t60 = t_abs - 60.0
    rec: dict[str, Any] = {
        "horizon_sec": int(horizon_sec),
        "checkpoint": t_abs,
        "status": "OK",
        "uneval_reason": None,
        "leak_n": 0,
        "bid_t": None,
        "bid_t_time": None,
        "mid_t": None,
        "mid_at_fill": None,
        "bid_return_from_fill": None,
        "mid_return_from_fill": None,
        "executable_mfe_to_t": None,
        "executable_mae_to_t": None,
        "max_bid_since_fill": None,
        "min_bid_since_fill": None,
        "bid_giveback_from_peak": None,
        "bid_t_minus_60": None,
        "bid_return_last_60s": None,
        "imbalance": None,
        "imb_p5_persist": None,
        "identity_pass": None,
    }
    sliced, leak_s = slice_board_le(board, t_abs)
    rec["leak_n"] = int(leak_s)
    q = quotes_asof(sliced, day=day, session=session, asof=t_abs)
    rec["leak_n"] += int(q.get("leak_n") or 0)
    if q.get("status") != "OK":
        rec["status"] = "PATH_NOT_EVALUABLE"
        rec["uneval_reason"] = "SESSION_LEAVE_OR_INCOMPLETE"
        return rec
    if rec["leak_n"]:
        rec["status"] = "PATH_NOT_EVALUABLE"
        rec["uneval_reason"] = "FUTURE_LEAK"
        return rec
    bid, bt, leak_b = last_valid_side(sliced, t_abs, side="bid")
    rec["leak_n"] += int(leak_b)
    if leak_b or bid is None or float(bid) <= 0 or fill_price <= 0:
        rec["status"] = "PATH_NOT_EVALUABLE"
        rec["uneval_reason"] = "NO_VALID_BID"
        return rec
    rec["bid_t"] = float(bid)
    rec["bid_t_time"] = bt
    rec["bid_return_from_fill"] = float(bid) / float(fill_price) - 1.0

    mid_fill = mid_at_or_before(sliced, float(fill_time))
    rec["mid_at_fill"] = mid_fill.get("mid")
    rec["leak_n"] += int(mid_fill.get("leak_n") or 0)
    rec["mid_t"] = q.get("mid")
    if rec["mid_at_fill"] and rec["mid_t"] and float(rec["mid_at_fill"]) > 0:
        rec["mid_return_from_fill"] = float(rec["mid_t"]) / float(rec["mid_at_fill"]) - 1.0

    mx, mn, leak_x = _bid_extrema(sliced, fill_t=float(fill_time), until=t_abs)
    rec["leak_n"] += int(leak_x)
    rec["max_bid_since_fill"] = mx
    rec["min_bid_since_fill"] = mn
    if mx is not None and fill_price > 0:
        rec["executable_mfe_to_t"] = float(mx) / float(fill_price) - 1.0
    if mn is not None and fill_price > 0:
        rec["executable_mae_to_t"] = float(mn) / float(fill_price) - 1.0
    if mx is not None and mx > 0:
        rec["bid_giveback_from_peak"] = float(bid) / float(mx) - 1.0

    if t60 >= float(fill_time) - 1e-12:
        b60, _, leak60 = last_valid_side(sliced, t60, side="bid")
        rec["leak_n"] += int(leak60)
        rec["bid_t_minus_60"] = b60
        if b60 is not None and float(b60) > 0:
            rec["bid_return_last_60s"] = float(bid) / float(b60) - 1.0

    sess_end = session_end_for_position(date=day, session=session, fill_time=float(fill_time))
    path = build_path(
        sliced,
        entry_price=float(fill_price),
        entry_t=float(fill_time),
        sess_end=min(float(sess_end), t_abs),
    )
    if path.get("ok"):
        path = attach_board_series(path, sliced)
        st = state_at_off(
            path,
            sliced,
            fill_t=float(fill_time),
            fill_price=float(fill_price),
            off=float(horizon_sec),
        )
        rec["imbalance"] = st.get("imbalance")
        rec["imb_p5_persist"] = imb_p5_persist_at_end(
            path,
            persist_sec=float(FROZEN_GUARD.get("persist_sec") or 5.0),
            thr=float(FROZEN_GUARD.get("imb_threshold") or -0.1),
        )

    ok_id = True
    if rec["executable_mfe_to_t"] is not None and rec["bid_return_from_fill"] is not None:
        if float(rec["executable_mfe_to_t"]) + 1e-9 < float(rec["bid_return_from_fill"]):
            ok_id = False
    if rec["executable_mae_to_t"] is not None and rec["bid_return_from_fill"] is not None:
        if float(rec["executable_mae_to_t"]) - 1e-9 > float(rec["bid_return_from_fill"]):
            ok_id = False
    if mx is not None and mx > 0 and rec["bid_giveback_from_peak"] is not None:
        lhs = float(bid) / float(mx) - 1.0
        if not rel_close(lhs, float(rec["bid_giveback_from_peak"])):
            ok_id = False
    rec["identity_pass"] = ok_id
    if rec["leak_n"]:
        rec["status"] = "PATH_NOT_EVALUABLE"
        rec["uneval_reason"] = "FUTURE_LEAK"
    return rec

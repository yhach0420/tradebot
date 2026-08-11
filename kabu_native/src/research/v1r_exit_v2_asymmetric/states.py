"""Causal decision-state reconstruction at EXIT decision horizons."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit
from research.v1r_exit_global_search.apply_exit import attach_board_series

HORIZONS = (5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 420, 600, 750)


def _valid_row(board: dict, i: int) -> bool:
    if board["special"][i]:
        return False
    fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
    if fresh > BOARD_FRESHNESS_SEC + 1e-12:
        return False
    bq = board["bid_qty"][i]
    bid = board["bid"][i]
    aq = board["ask_qty"][i]
    ask = board["ask"][i]
    if not (np.isfinite(bq) and bq >= MIN_QTY and np.isfinite(bid) and bid > 0):
        return False
    if not (np.isfinite(aq) and aq >= MIN_QTY and np.isfinite(ask) and ask > 0):
        return False
    return True


def exit_at_horizon(path: dict[str, Any], off: float) -> dict[str, Any]:
    """FIRST_VALID_EXECUTABLE_BUY1_AT_OR_AFTER_TRIGGER semantics."""
    ex = canonical_fixed_exit(path, float(off))
    if not ex.get("ok"):
        return {"ok": False}
    return {
        "ok": True,
        "exit_time": float(ex["exit_time"]),
        "exit_off": float(ex["exit_off"]),
        "exit_ret_bps": float(ex["exit_ret_bps"]),
        "reason": ex.get("reason") or "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET",
    }


def state_at_off(
    path: dict[str, Any],
    board: Optional[dict[str, np.ndarray]],
    *,
    fill_t: float,
    fill_price: float,
    off: float,
) -> dict[str, Any]:
    """Causal state using only information at/before fill+off."""
    out: dict[str, Any] = {
        "off": float(off),
        "ok": False,
        "ret": None,
        "mfe": None,
        "mae": None,
        "dd_from_mfe": None,
        "rebound_from_mae": None,
        "time_since_high": None,
        "time_since_low": None,
        "buy1_qty": None,
        "sell1_qty": None,
        "imbalance": None,
        "spread_bps": None,
        "bid_qty_chg": None,
        "bid_downticks": None,
        "event_rate": None,
        "pos_event_rate": None,
        "neg_event_rate": None,
        "sell_persist": False,
        "recovery_persist": False,
    }
    if not path.get("ok") or path["offs"].size == 0:
        return out
    offs, rets = path["offs"], path["rets"]
    mask = offs <= float(off) + 1e-12
    if not np.any(mask):
        return out
    rr = rets[mask]
    oo = offs[mask]
    mfe_i = int(np.argmax(rr))
    mae_i = int(np.argmin(rr))
    mfe = float(rr[mfe_i])
    mae = float(rr[mae_i])
    ret = float(rr[-1])
    out.update({
        "ok": True,
        "ret": ret,
        "mfe": mfe,
        "mae": mae,
        "dd_from_mfe": float(mfe - ret),
        "rebound_from_mae": float(ret - mae),
        "time_since_high": float(oo[-1] - oo[mfe_i]),
        "time_since_low": float(oo[-1] - oo[mae_i]),
    })

    # board / flow from attached series if present, else from board
    imb = path.get("imb")
    spr = path.get("spread")
    bq = path.get("bid_qty")
    er = path.get("event_rate")
    j = int(np.searchsorted(offs, float(off), side="left"))
    j = min(j, offs.size - 1)
    if imb is not None and imb.size == offs.size and np.isfinite(imb[j]):
        out["imbalance"] = float(imb[j])
    if spr is not None and spr.size == offs.size and np.isfinite(spr[j]):
        out["spread_bps"] = float(spr[j])
    if bq is not None and bq.size == offs.size and np.isfinite(bq[j]):
        out["buy1_qty"] = float(bq[j])
        bq0 = path.get("bid_qty0")
        if bq0:
            out["bid_qty_chg"] = float(bq[j]) - float(bq0)
    if er is not None and er.size == offs.size and np.isfinite(er[j]):
        out["event_rate"] = float(er[j])

    if board is not None and board["t"].size:
        target = fill_t + float(off)
        t = board["t"]
        i1 = int(np.searchsorted(t, target, side="right"))
        w0 = max(fill_t, target - 30.0)
        i0 = int(np.searchsorted(t, w0, side="left"))
        bids = []
        for k in range(i0, i1):
            if _valid_row(board, k):
                bids.append(float(board["bid"][k]))
                if out.get("sell1_qty") is None:
                    out["sell1_qty"] = float(board["ask_qty"][k])
        if len(bids) >= 2:
            bd = sum(1 for a, b in zip(bids, bids[1:]) if b < a - 1e-12)
            bu = sum(1 for a, b in zip(bids, bids[1:]) if b > a + 1e-12)
            out["bid_downticks"] = bd
            out["neg_event_rate"] = float(bd) / 30.0
            out["pos_event_rate"] = float(bu) / 30.0

    # concept flags (causal)
    sell = False
    if mae is not None and ret is not None:
        sell = mae <= -25 and ret <= -15
    if out.get("bid_downticks") is not None and out["bid_downticks"] >= 3 and mae is not None and mae <= -15:
        sell = True
    out["sell_persist"] = bool(sell)

    recovery = False
    if mfe >= 20 and ret >= 10 and ret > mae + 25:
        recovery = True
    out["recovery_persist"] = bool(recovery)
    return out


def build_trade_bundle(
    fill: dict[str, Any],
    path: dict[str, Any],
    board: Optional[dict[str, np.ndarray]],
) -> dict[str, Any]:
    """Attach board series + states + counterfactual horizons."""
    if board is not None:
        path = attach_board_series(path, board)
    states = {
        h: state_at_off(
            path, board,
            fill_t=float(fill["fill_time"]),
            fill_price=float(fill["fill_price"]),
            off=float(h),
        )
        for h in HORIZONS
    }
    cf: dict[str, Any] = {}
    for h in (600, 750):
        ex = exit_at_horizon(path, float(h))
        cf[h] = ex
    ret600 = float(cf[600]["exit_ret_bps"]) if cf[600].get("ok") else None
    ret750 = float(cf[750]["exit_ret_bps"]) if cf[750].get("ok") else None
    return {
        "date": fill["date"],
        "symbol": fill["symbol"],
        "session": fill.get("session"),
        "anchor_id": fill.get("anchor_id") or fill.get("anchor") or fill.get("signal_anchor"),
        "fill_time": float(fill["fill_time"]),
        "fill_price": float(fill["fill_price"]),
        "path": path,
        "states": states,
        "cf600": cf[600],
        "cf750": cf[750],
        "ret600": ret600,
        "ret750": ret750,
        "delta_750_vs_600_bps": (ret750 - ret600) if (ret600 is not None and ret750 is not None) else None,
    }

"""V1R EXIT Scenario Research — post-FILL reconstruction, taxonomy, causal states."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x35_passive_exit.paths import build_path, path_metrics

# Decision horizons (seconds from fill)
HORIZONS = (0, 1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600)


def _valid_row(board: dict, i: int, *, need_ask: bool = False) -> bool:
    if board["special"][i]:
        return False
    fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
    if fresh > BOARD_FRESHNESS_SEC + 1e-12:
        return False
    bq = board["bid_qty"][i]
    bid = board["bid"][i]
    if not np.isfinite(bq) or bq < MIN_QTY or not np.isfinite(bid) or bid <= 0:
        return False
    if need_ask:
        aq = board["ask_qty"][i]
        ask = board["ask"][i]
        if not np.isfinite(aq) or aq < MIN_QTY or not np.isfinite(ask) or ask <= 0:
            return False
    return True


def snapshot_at(
    board: dict[str, np.ndarray],
    *,
    fill_t: float,
    fill_price: float,
    off_sec: float,
) -> dict[str, Any]:
    """Causal snapshot at fill_t + off_sec using only events with t <= fill_t+off."""
    t = board["t"]
    target = fill_t + float(off_sec)
    j = int(np.searchsorted(t, target, side="right") - 1)
    out: dict[str, Any] = {
        "off": float(off_sec),
        "ok": False,
        "buy1": None,
        "sell1": None,
        "mid": None,
        "ret_bps": None,
        "spread_bps": None,
        "imbalance": None,
        "buy1_qty": None,
        "sell1_qty": None,
        "fresh_sec": None,
        "event_rate_30s": None,
        "bid_downticks_30s": None,
        "bid_upticks_30s": None,
        "ask_downticks_30s": None,
        "ask_upticks_30s": None,
        "bid_qty_chg_30s": None,
    }
    if j < 0 or t.size == 0:
        return out

    # walk back to last valid both-sides quote at/before target
    i = j
    while i >= 0 and float(t[i]) >= fill_t - 1e-12:
        if _valid_row(board, i, need_ask=True):
            bid = float(board["bid"][i])
            ask = float(board["ask"][i])
            bq = float(board["bid_qty"][i])
            aq = float(board["ask_qty"][i])
            mid = (bid + ask) / 2.0
            out.update({
                "ok": True,
                "buy1": bid,
                "sell1": ask,
                "mid": mid,
                "ret_bps": (bid / fill_price - 1.0) * 10000.0 if fill_price > 0 else None,
                "spread_bps": (ask - bid) / mid * 10000.0 if mid > 0 else None,
                "imbalance": (bq - aq) / (bq + aq) if (bq + aq) > 0 else None,
                "buy1_qty": bq,
                "sell1_qty": aq,
                "fresh_sec": float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else None,
            })
            break
        i -= 1

    # flow window [target-30, target] ∩ [fill_t, target]
    w0 = max(fill_t, target - 30.0)
    i0 = int(np.searchsorted(t, w0, side="left"))
    i1 = int(np.searchsorted(t, target, side="right"))
    if i1 > i0:
        out["event_rate_30s"] = float(i1 - i0) / 30.0
        bids = []
        asks = []
        bqs = []
        for k in range(i0, i1):
            if _valid_row(board, k, need_ask=True):
                bids.append(float(board["bid"][k]))
                asks.append(float(board["ask"][k]))
                bqs.append(float(board["bid_qty"][k]))
        if len(bids) >= 2:
            bd = sum(1 for a, b in zip(bids, bids[1:]) if b < a - 1e-12)
            bu = sum(1 for a, b in zip(bids, bids[1:]) if b > a + 1e-12)
            ad = sum(1 for a, b in zip(asks, asks[1:]) if b < a - 1e-12)
            au = sum(1 for a, b in zip(asks, asks[1:]) if b > a + 1e-12)
            out["bid_downticks_30s"] = bd
            out["bid_upticks_30s"] = bu
            out["ask_downticks_30s"] = ad
            out["ask_upticks_30s"] = au
            out["bid_qty_chg_30s"] = bqs[-1] - bqs[0]
    return out


def reconstruct_trade(
    fill: dict[str, Any],
    board: dict[str, np.ndarray],
    *,
    sess_end: float,
) -> dict[str, Any]:
    """Phase A reconstruction for one accepted FILL."""
    fill_t = float(fill["fill_time"])
    fill_px = float(fill["fill_price"])
    path = build_path(board, entry_price=fill_px, entry_t=fill_t, sess_end=sess_end)
    met = path_metrics(path) if path.get("ok") else {"ok": False}

    snaps = {h: snapshot_at(board, fill_t=fill_t, fill_price=fill_px, off_sec=h) for h in HORIZONS}

    # causal MFE/MAE/giveback at each horizon (path prefix)
    prefix_stats = {}
    if path.get("ok") and path["offs"].size:
        offs, rets = path["offs"], path["rets"]
        for h in HORIZONS:
            mask = offs <= h + 1e-12
            if not np.any(mask):
                prefix_stats[h] = {"mfe": None, "mae": None, "giveback": None, "ret": None}
                continue
            rr = rets[mask]
            oo = offs[mask]
            mfe_i = int(np.argmax(rr))
            mae_i = int(np.argmin(rr))
            mfe = float(rr[mfe_i])
            mae = float(rr[mae_i])
            after = rr[mfe_i:]
            giveback = float(mfe - float(np.min(after))) if mfe > 0 and after.size else 0.0
            prefix_stats[h] = {
                "mfe": mfe,
                "mae": mae,
                "giveback": giveback,
                "ret": float(rr[-1]),
                "time_to_mfe": float(oo[mfe_i]),
                "time_to_mae": float(oo[mae_i]),
            }
    else:
        for h in HORIZONS:
            prefix_stats[h] = {"mfe": None, "mae": None, "giveback": None, "ret": None}

    return {
        "date": fill["date"],
        "symbol": fill["symbol"],
        "session": fill.get("session"),
        "fill_time": fill_t,
        "fill_price": fill_px,
        "signal_time": float(fill.get("signal_time") or 0),
        "alloc_score": fill.get("alloc_score"),
        "fixed600_ret_bps": fill.get("canonical_exit_ret_bps") or fill.get("realized_ret_bps"),
        "fixed600_pnl_yen": fill.get("realized_pnl_yen"),
        "fixed600_exit_time": fill.get("canonical_exit_time"),
        "path_ok": bool(path.get("ok")),
        "path_metrics": met,
        "snaps": snaps,
        "prefix": prefix_stats,
    }


def label_taxonomy(recon: dict[str, Any]) -> str:
    """
    Research labels using full path (future OK for taxonomy only).
    A: sell→absorb→rebound→sustained rise
    B: rebound then large giveback
    C: persistent selling / no recovery
    D: range / unclear
    """
    met = recon.get("path_metrics") or {}
    pref = recon.get("prefix") or {}
    if not met.get("ok"):
        return "D"

    mfe = float(met.get("mfe") or 0)
    mae = float(met.get("mae") or 0)
    final = float(
        met.get("final_ret")
        if met.get("final_ret") is not None
        else (pref.get(600) or {}).get("ret") or 0
    )
    giveback = float(met.get("giveback_to_end") or met.get("max_giveback") or 0)
    t_mfe = float(met.get("time_to_mfe") or 0)
    t_mae = float(met.get("time_to_mae") or 0)

    early = pref.get(30) or {}
    early_mae = early.get("mae")
    early_ret = early.get("ret")
    mid60 = pref.get(60) or {}

    # C: early deep adverse + never recovers
    if early_mae is not None and early_mae <= -40 and final <= -20 and mfe < 30:
        return "C"
    if mae <= -80 and final <= -40 and mfe < 40:
        return "C"

    # B: had meaningful MFE then large giveback
    if mfe >= 50 and giveback >= 0.55 * mfe and final < 0.4 * mfe:
        return "B"

    # A: adverse or flat early, then rebound to sustained positive
    sold_early = (early_mae is not None and early_mae <= -15) or (
        early_ret is not None and early_ret <= -10
    )
    if sold_early and final >= 30 and mfe >= 40 and t_mfe >= 20:
        return "A"
    if final >= 50 and mfe >= 50 and giveback <= 0.45 * mfe:
        return "A"

    # weak A-like recovery
    if (mid60.get("ret") or -999) > (early_ret or 0) + 20 and final >= 20:
        return "A"

    return "D"


def causal_state_at(recon: dict[str, Any], off: float) -> dict[str, Any]:
    """Concept-level states using only data <= fill+off."""
    pref = (recon.get("prefix") or {}).get(int(off) if off == int(off) else off) or {}
    # nearest horizon key
    if not pref:
        hs = sorted((recon.get("prefix") or {}).keys())
        hs2 = [h for h in hs if h <= off]
        pref = (recon.get("prefix") or {}).get(hs2[-1]) if hs2 else {}
    snap = (recon.get("snaps") or {}).get(int(off) if int(off) in (recon.get("snaps") or {}) else None)
    if snap is None:
        # pick closest horizon <= off
        hs = [h for h in HORIZONS if h <= off]
        snap = (recon.get("snaps") or {}).get(hs[-1]) if hs else {}

    mfe = pref.get("mfe")
    mae = pref.get("mae")
    ret = pref.get("ret")
    giveback = pref.get("giveback")
    imb = snap.get("imbalance") if snap else None
    bid_dn = snap.get("bid_downticks_30s") if snap else None
    bid_up = snap.get("bid_upticks_30s") if snap else None
    bq_chg = snap.get("bid_qty_chg_30s") if snap else None
    er = snap.get("event_rate_30s") if snap else None

    sell_persist = False
    if mae is not None and ret is not None:
        sell_persist = mae <= -25 and ret <= -15
    if bid_dn is not None and bid_up is not None and bid_dn >= bid_up + 2:
        sell_persist = sell_persist or (mae is not None and mae <= -15)

    recovery = False
    if mfe is not None and mae is not None and ret is not None:
        recovery = mfe >= 20 and ret >= 10 and ret > mae + 25
    if bid_up is not None and bid_dn is not None and bid_up > bid_dn and ret is not None and ret >= 5:
        recovery = True

    completion = False
    if mfe is not None and giveback is not None and mfe >= 40:
        completion = giveback >= 0.45 * mfe and (ret is not None and ret <= 0.6 * mfe)

    return {
        "off": off,
        "sell_pressure_persist": sell_persist,
        "recovery_continuation": recovery,
        "completion_exhaustion": completion,
        "mfe": mfe,
        "mae": mae,
        "ret": ret,
        "giveback": giveback,
        "imbalance": imb,
        "bid_dn": bid_dn,
        "bid_up": bid_up,
        "bid_qty_chg": bq_chg,
        "event_rate": er,
    }

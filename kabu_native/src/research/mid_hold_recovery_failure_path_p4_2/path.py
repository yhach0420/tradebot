"""Attach causal trajectory descriptors. No threshold / no composite Gate."""
from __future__ import annotations

from typing import Any, Optional

from research.mid_hold_recovery_failure_path_p4_2 import CHECKPOINTS_SEC


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def pivot_trades(rows: list[dict[str, Any]]) -> dict[str, dict[int, dict[str, Any]]]:
    out: dict[str, dict[int, dict[str, Any]]] = {}
    for r in rows:
        tid = str(r.get("trade_id") or "")
        if not tid:
            continue
        h = int(r.get("horizon_sec") or 0)
        out.setdefault(tid, {})[h] = r
    return out


def attach_trajectory(by_h: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fill trajectory fields in-place copies. Causal: only earlier checkpoints + current."""
    prev_h = None
    uw_count = 0
    consec = 0
    out: dict[int, dict[str, Any]] = {}
    r120 = by_h.get(120) or {}
    br120 = _f(r120.get("bid_return_from_fill")) if r120.get("eligible") else None

    for h in CHECKPOINTS_SEC:
        rec = dict(by_h.get(h) or {})
        rec["delta_bid_120_to_t"] = None
        rec["delta_bid_prev_checkpoint"] = None
        rec["delta_mfe_prev_checkpoint"] = None
        rec["delta_mae_prev_checkpoint"] = None
        rec["new_low_created"] = None
        rec["new_mfe_created"] = None
        rec["underwater_checkpoint_count"] = None
        rec["consecutive_underwater_count"] = None
        if rec.get("rebound_from_low_t") is None and rec.get("bid_t") and rec.get("min_bid_since_fill"):
            mn = _f(rec.get("min_bid_since_fill"))
            bid = _f(rec.get("bid_t"))
            if mn is not None and mn > 0 and bid is not None:
                rec["rebound_from_low_t"] = bid / mn - 1.0

        if rec.get("eligible") is True:
            br = _f(rec.get("bid_return_from_fill"))
            if br is not None and br < 0:
                uw_count += 1
                consec += 1
            else:
                consec = 0
            rec["underwater_checkpoint_count"] = int(uw_count)
            rec["consecutive_underwater_count"] = int(consec)
            if br120 is not None and br is not None:
                rec["delta_bid_120_to_t"] = br - br120
            if prev_h is not None:
                prev = out.get(prev_h) or {}
                pbr = _f(prev.get("bid_return_from_fill"))
                if pbr is not None and br is not None:
                    rec["delta_bid_prev_checkpoint"] = br - pbr
                pmfe = _f(prev.get("executable_mfe_to_t"))
                mfe = _f(rec.get("executable_mfe_to_t"))
                if pmfe is not None and mfe is not None:
                    rec["delta_mfe_prev_checkpoint"] = mfe - pmfe
                    rec["new_mfe_created"] = bool(mfe > pmfe + 1e-12)
                pmae = _f(prev.get("executable_mae_to_t"))
                mae = _f(rec.get("executable_mae_to_t"))
                if pmae is not None and mae is not None:
                    rec["delta_mae_prev_checkpoint"] = mae - pmae
                    rec["new_low_created"] = bool(mae < pmae - 1e-12)
        out[h] = rec
        if rec.get("eligible") is True:
            prev_h = h
    return out


def cohort_flags(rec120: dict[str, Any], *, p41_ids: set[str]) -> dict[str, Any]:
    elig = rec120.get("eligible") is True
    br = _f(rec120.get("bid_return_from_fill"))
    adverse = bool(elig and br is not None and br < 0)
    win = bool(rec120.get("FINAL_WIN"))
    loss = bool(rec120.get("FINAL_LOSS"))
    draw = bool(rec120.get("FINAL_DRAW"))
    tid = str(rec120.get("trade_id") or "")
    return {
        "cohort_A_eligible": elig,
        "cohort_B_adverse120": adverse,
        "cohort_C_p41_trigger": tid in p41_ids,
        "RECOVERING_WINNER": bool(adverse and win),
        "PERSISTENT_FAILURE": bool(adverse and loss),
        "ADVERSE_DRAW": bool(adverse and draw),
        "bid_return_120": br,
    }


def build_trade_paths(
    rows: list[dict[str, Any]],
    *,
    p41_ids: set[str],
) -> list[dict[str, Any]]:
    pivoted = pivot_trades(rows)
    trades: list[dict[str, Any]] = []
    for tid, by_h in pivoted.items():
        rec120 = by_h.get(120) or {}
        flags = cohort_flags(rec120, p41_ids=p41_ids)
        traj = attach_trajectory(by_h)
        for h, rec in traj.items():
            rec.update(flags)
            rec["trade_id"] = tid
        trades.append({"trade_id": tid, "by_horizon": traj, **flags, "date": rec120.get("date"), "symbol": rec120.get("symbol")})
    return trades

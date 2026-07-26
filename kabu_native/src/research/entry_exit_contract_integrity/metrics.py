"""Corrected MFE capture + economic/structural success (evaluation-only)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.entry_exit_contract.constants import ROUNDTRIP_COST_PCT
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import ExitSim
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.path_mfe import PathBar, compute_executable_mfe


def pnl_pct_5bps(entry: float, exit_px: float) -> float:
    """Percent return after 5bps roundtrip — NOT yen."""
    if entry <= 0:
        return 0.0
    return (exit_px - entry) / entry * 100.0 - ROUNDTRIP_COST_PCT


def mfe_capture_block(
    c: EntryContract,
    path: Sequence[PathBar],
    ex: ExitSim,
) -> dict[str, Any]:
    fe = FixedEntry(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        entry_price=c.entry_price,
        entry_method=c.strategy_id,
        cohort=c.strategy_id,
        setup_id=c.setup_id,
    )
    mfe = compute_executable_mfe(fe, path)
    # executable_mfe_pct_5bps: percent points (same unit as ROUNDTRIP_COST_PCT)
    mfe_pct = mfe.mfe_5bps  # already in percent points in path_mfe
    actual_pct = pnl_pct_5bps(c.entry_price, ex.exit_price)
    capture_raw = None
    capture_pos = None
    zero_neg = False
    if mfe_pct is None:
        pass
    elif mfe_pct <= 0:
        zero_neg = True
    else:
        capture_raw = actual_pct / mfe_pct
        capture_pos = capture_raw
    pos_to_neg = bool(mfe_pct is not None and mfe_pct > 0 and actual_pct <= 0)
    return {
        "executable_mfe_pct_5bps": mfe_pct,
        "actual_pnl_pct_5bps": actual_pct,
        "actual_pnl_yen_5bps": ex.pnl_5bps,
        "capture_ratio_raw": capture_raw,
        "capture_ratio_positive_mfe_only": capture_pos,
        "positive_to_negative_reversal": pos_to_neg,
        "zero_or_negative_mfe": zero_neg,
        "executable_mae_pct_5bps": mfe.mae_5bps,
        "quote_evaluable": mfe.quote_evaluable,
    }


def economic_success_block(
    c: EntryContract,
    path: Sequence[PathBar],
    ex: ExitSim,
    mfe_blk: dict[str, Any],
    *,
    bid_qty_at_exit: Optional[float],
) -> dict[str, Any]:
    structural = bool(ex.expected_achieved)
    mfe_pct = mfe_blk.get("executable_mfe_pct_5bps")
    # economic: MFE exceeds cost+spread proxy; cost already in mfe_5bps; require mfe > 0
    # "spreadと5bpsを超える" → mfe_pct > 0 (already net of 5bps) and preferably raw mfe > spread
    spread = None
    for b in path:
        if b.t >= ex.exit_time:
            spread = b.spread_bps
            break
    if spread is None and path:
        spread = path[0].spread_bps
    # minimum profit zone held: time with bid pnl_pct_5bps > 0 at least 1 bar
    held_profit = False
    for b in path:
        if b.t > ex.exit_time:
            break
        if b.bid is not None and b.bid > 0:
            if pnl_pct_5bps(c.entry_price, b.bid) > 0:
                held_profit = True
                break
    bid_ok = bid_qty_at_exit is None or bid_qty_at_exit >= 100
    bid_ne = bid_qty_at_exit is None
    economic = bool(
        mfe_pct is not None
        and mfe_pct > 0
        and held_profit
        and (bid_ok or bid_ne)  # NOT_EVALUABLE qty allowed per spec
        and mfe_blk.get("quote_evaluable")
    )
    # tighten: must exceed spread in bps if known (spread_bps/100 = pct points roughly: 10bps=0.10%)
    if economic and spread is not None and mfe_pct is not None:
        # spread_bps/100 → percent points; require gross MFE (pre 5bps) exceeds spread
        if (mfe_pct + ROUNDTRIP_COST_PCT) < float(spread) / 100.0:
            economic = False

    actual_yen = float(ex.pnl_5bps)
    actual_pct = float(mfe_blk.get("actual_pnl_pct_5bps") or 0)
    cap = mfe_blk.get("capture_ratio_positive_mfe_only")
    if not economic and not structural:
        label = "NEVER_ACTIVATED_OR_FAILED"
    elif structural and not economic:
        label = "STRUCTURAL_ONLY"
    elif economic and actual_yen > 0 and (cap is None or cap >= 0.50):
        label = "CAPTURED_SUCCESS"
    elif economic and (actual_yen <= 0 or (cap is not None and cap < 0.50)):
        label = "UNDER_CAPTURED_SUCCESS"
    else:
        label = "STRUCTURAL_ONLY"

    return {
        "structural_success": structural,
        "economic_success": economic,
        "captured_success": label == "CAPTURED_SUCCESS",
        "under_captured_success": label == "UNDER_CAPTURED_SUCCESS",
        "success_label": label,
        "profit_zone_held": held_profit,
        "bid_qty_ok_or_ne": bid_ok or bid_ne,
        "spread_bps_at_exit": spread,
    }

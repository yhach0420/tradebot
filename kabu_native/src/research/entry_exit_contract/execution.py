"""Bid execution realism / slippage (no interpolation)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.pbv2_zero_base_revalidation.util import pnl_5bps
from research.price_flow_exit.path_mfe import PathBar


def _tick_size(px: float) -> float:
    if px >= 5000:
        return 1.0
    if px >= 1000:
        return 0.5
    if px >= 100:
        return 0.1
    return 0.05


def execution_realism(c: EntryContract, path: Sequence[PathBar], *, exit_time, exit_price: float) -> dict[str, Any]:
    # find bar at/after exit
    idx = None
    for i, b in enumerate(path):
        if b.t >= exit_time:
            idx = i
            break
    if idx is None:
        return {"status": "NOT_EVALUABLE"}
    b = path[idx]
    bid = b.bid
    bid_qty = b.bid_qty
    spread = b.spread_bps
    tick = _tick_size(c.entry_price)
    sellable = None if bid_qty is None else bool(bid_qty >= 100)
    # next event bid
    next_bid = path[idx + 1].bid if idx + 1 < len(path) else None
    next_mode = "OBSERVED" if next_bid is not None else "NOT_EVALUABLE"

    def bid_after(sec: float) -> tuple[Optional[float], str]:
        target = exit_time + timedelta(seconds=sec)
        for j in range(idx, len(path)):
            if path[j].t >= target and path[j].bid is not None:
                return float(path[j].bid), "OBSERVED"
        # no interpolation — use next event if any
        if next_bid is not None and sec <= 1.0:
            return float(next_bid), "APPROXIMATED_FROM_NEXT_EVENT"
        return None, "NOT_EVALUABLE"

    b100, m100 = bid_after(0.1)
    b500, m500 = bid_after(0.5)
    b1s, m1s = bid_after(1.0)
    slip1 = exit_price - tick if exit_price else None
    slip2 = exit_price - 2 * tick if exit_price else None

    def delay_pnl(px: Optional[float]) -> Optional[float]:
        if px is None:
            return None
        return pnl_5bps(c.entry_price, px)

    return {
        "status": "OK" if bid is not None else "NOT_EVALUABLE",
        "bid_at_decision": bid,
        "bid_qty": bid_qty,
        "sellable_100": sellable,
        "spread_bps": spread,
        "next_push_bid": next_bid,
        "next_push_mode": next_mode,
        "bid_100ms": b100,
        "bid_100ms_mode": m100,
        "bid_500ms": b500,
        "bid_500ms_mode": m500,
        "bid_1s": b1s,
        "bid_1s_mode": m1s,
        "exit_1tick_slip": slip1,
        "exit_2tick_slip": slip2,
        "pnl_1tick_slip": delay_pnl(slip1),
        "pnl_2tick_slip": delay_pnl(slip2),
        "pnl_500ms_delay": delay_pnl(b500),
        "pnl_1s_delay": delay_pnl(b1s),
        "tick_size": tick,
    }

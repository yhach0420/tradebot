"""Future-only opportunity labels from canonical Bid path after Ask entry."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from research.canonical_zero_base.canonical_loader import Tick
from research.canonical_zero_base.constants import COST_BPS, LARGE_RISE_LEVELS, LOT


def opportunity_from_path(
    ticks: Sequence[Tick],
    entry_idx: int,
    *,
    entry_ask: float,
) -> dict[str, Any]:
    """Labels use future ticks only (j > entry_idx). Sell at future canonical bid."""
    if entry_ask <= 0 or entry_idx < 0 or entry_idx >= len(ticks):
        return {"evaluable": False}
    horizons = {5: 0.0, 15: 0.0, 30: 0.0, 60: 0.0, 120: 0.0, 300: 0.0}
    mae = 0.0
    ever_pos = False
    never = True
    t0 = ticks[entry_idx].ts
    t_mfe = None
    mfe_global = 0.0
    for j in range(entry_idx + 1, min(len(ticks), entry_idx + 400)):
        t = ticks[j]
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        pnl_pct = (bid - entry_ask) / entry_ask * 100.0
        mae = min(mae, pnl_pct)
        if pnl_pct > mfe_global:
            mfe_global = pnl_pct
            t_mfe = t.ts
        if pnl_pct > 0:
            ever_pos = True
            never = False
        dt = (t.ts - t0).total_seconds()
        for h in horizons:
            if dt <= h:
                horizons[h] = max(horizons[h], pnl_pct)
        if dt > 300:
            break
    cost_pct = 2 * COST_BPS / 100.0  # rough pct of notional for roundtrip in %
    # cost in price terms ≈ entry * 5bps*2 / entry * 100 = 0.10% for 5bps*2
    cost_pct = COST_BPS * 2 / 100.0  # in percent points of price? 5bps*2 = 0.10%
    pos_after_5bps = mfe_global > cost_pct
    tick_sz = 1.0 if entry_ask < 3000 else (5.0 if entry_ask < 5000 else 10.0)
    pos_after_1tick = mfe_global * entry_ask / 100.0 > tick_sz
    large = {f"large_rise_{lv}": mfe_global >= lv for lv in LARGE_RISE_LEVELS}
    return {
        "evaluable": True,
        "mfe_5": horizons[5],
        "mfe_15": horizons[15],
        "mfe_30": horizons[30],
        "mfe_60": horizons[60],
        "mfe_120": horizons[120],
        "mfe_300": horizons[300],
        "mae": mae,
        "time_to_mfe_sec": (t_mfe - t0).total_seconds() if t_mfe else None,
        "positive_after_5bps": pos_after_5bps,
        "positive_after_1tick": pos_after_1tick,
        "never_profitable": never,
        "exec_pos": ever_pos,
        "no_progress": mfe_global < 0.3,
        **large,
    }

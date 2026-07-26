"""Execution realism R0–R5 without interpolation for timed windows."""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.pbv2_zero_base_revalidation.util import pnl_5bps
from research.price_flow_exit.path_mfe import PathBar
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.trades import SimTrade


def _tick_size(px: float) -> float:
    if px >= 5000:
        return 1.0
    if px >= 1000:
        return 0.5
    if px >= 100:
        return 0.1
    return 0.05


def execution_ladder(c: EntryContract, path: Sequence[PathBar], *, exit_time, exit_price: float) -> dict[str, Any]:
    idx = None
    for i, b in enumerate(path):
        if b.t >= exit_time:
            idx = i
            break
    if idx is None:
        return {"status": "NOT_EVALUABLE"}

    b0 = path[idx]
    tick = _tick_size(c.entry_price)
    next_bid = path[idx + 1].bid if idx + 1 < len(path) else None
    next_delay = None
    if idx + 1 < len(path):
        next_delay = (path[idx + 1].t - exit_time).total_seconds()

    def bid_within(sec: float) -> tuple[Optional[float], str, Optional[float]]:
        """Strict: only OBSERVED bar with t in (exit, exit+sec]. No next-event fill-in."""
        target = exit_time + timedelta(seconds=sec)
        for j in range(idx + 1, len(path)):
            if path[j].t > target:
                break
            if path[j].bid is not None and path[j].bid > 0:
                delay = (path[j].t - exit_time).total_seconds()
                return float(path[j].bid), "OBSERVED", delay
        return None, "NOT_EVALUABLE", None

    b500, m500, d500 = bid_within(0.5)
    b1s, m1s, d1s = bid_within(1.0)

    # R0 decision bid
    r0 = float(b0.bid) if b0.bid is not None and b0.bid > 0 else None
    r1 = (exit_price - tick) if exit_price else None
    r2 = (exit_price - 2 * tick) if exit_price else None
    r3 = float(next_bid) if next_bid is not None and next_bid > 0 else None

    def yen(px: Optional[float]) -> Optional[float]:
        return None if px is None else pnl_5bps(c.entry_price, px)

    return {
        "status": "OK" if r0 is not None else "NOT_EVALUABLE",
        "bid_qty": b0.bid_qty,
        "spread_bps": b0.spread_bps,
        "tick_size": tick,
        "observation_delay_next_sec": next_delay,
        "observation_delay_500ms_sec": d500,
        "R0_bid": r0,
        "R0_pnl_5bps": yen(r0),
        "R1_1tick": r1,
        "R1_pnl_5bps": yen(r1),
        "R2_2tick": r2,
        "R2_pnl_5bps": yen(r2),
        "R3_next_event_bid": r3,
        "R3_mode": "OBSERVED" if r3 is not None else "NOT_EVALUABLE",
        "R3_pnl_5bps": yen(r3),
        "R4_500ms_bid": b500,
        "R4_mode": m500,
        "R4_pnl_5bps": yen(b500),
        "R5_1s_bid": b1s,
        "R5_mode": m1s,
        "R5_pnl_5bps": yen(b1s),
    }


def summarize_reality(rows: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    """Aggregate R* pnl series into pnl/PF/DD/days."""
    pnls = []
    trades = []
    delays = []
    for r in rows:
        ladder = r.get("execution") or {}
        y = ladder.get(key)
        if y is None:
            continue
        pnls.append(float(y))
        if ladder.get("observation_delay_next_sec") is not None:
            delays.append(float(ladder["observation_delay_next_sec"]))
        trades.append(
            SimTrade(
                day=r["day"],
                symbol=r["symbol"],
                entry_time=datetime.fromisoformat(r["entry_time"]),
                exit_time=datetime.fromisoformat(r["exit_time"]),
                entry_price=float(r["entry_price"]),
                exit_price=float(r.get("exit_price") or r["entry_price"]),
                exit_reason=str(r.get("exit_reason") or ""),
                pnl_5bps=float(y),
                hold_sec=float(r.get("hold_sec") or 0),
                entry_method=str(r.get("strategy_id") or ""),
                cohort=str(r.get("strategy_id") or ""),
                setup_id=str(r.get("setup_id") or ""),
                impulse_episode_id=str(r.get("episode_id") or ""),
                breakout_episode_id=str(r.get("episode_id") or ""),
                pbv2=False,
                vcie=True,
                mode="M2",
                session=str(r.get("session") or "AM"),
            )
        )
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    dd = summarize_dd(trades) if trades else {}
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t.day] = by_day.get(t.day, 0.0) + t.pnl_5bps
    return {
        "evaluable_n": len(pnls),
        "pnl_5bps": round(float(block.get("total_pnl_5bps") or 0), 2),
        "PF_5bps": block.get("PF_5bps"),
        "trade_sequence_max_dd": dd.get("trade_sequence_max_dd"),
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v < 0),
        "median_obs_delay_sec": round(statistics.median(delays), 4) if delays else None,
        "p90_obs_delay_sec": round(sorted(delays)[int(0.9 * (len(delays) - 1))], 4) if delays else None,
    }

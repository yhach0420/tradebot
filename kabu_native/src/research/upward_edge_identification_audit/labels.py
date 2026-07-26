"""First-passage labels — bid path vs ask entry, no side-guess on same event."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.upward_edge_identification_audit.constants import BARRIERS, COST_BPS
from research.upward_edge_identification_audit.loader import Tick


@dataclass
class LabelRow:
    sample_id: str
    barrier: str
    entry_ask: float
    entry_bid: float
    entry_spread: Optional[float]
    up_barrier: float
    down_barrier: float
    horizon_sec: float
    first_result: str
    first_hit_time: Optional[datetime]
    first_hit_sec: Optional[float]
    max_future_bid: Optional[float]
    min_future_bid: Optional[float]
    MFE_bps: Optional[float]
    MAE_bps: Optional[float]
    terminal_return_bps: Optional[float]
    cost_adjusted_return_bps: Optional[float]
    events_observed: int
    data_complete: bool


def _bps(a: float, b: float) -> float:
    return (b - a) / a * 10000.0 if a > 0 else 0.0


def label_first_passage(
    ticks: Sequence[Tick],
    i: int,
    sample_id: str,
    barrier_id: str,
    entry_ask: float,
    entry_bid: float,
    spread_bps: Optional[float],
) -> LabelRow:
    spec = BARRIERS[barrier_id]
    up_bps = spec["up_bps"]
    down_bps = spec["down_bps"]
    horizon = spec["horizon_sec"]
    up_px = entry_ask * (1.0 + up_bps / 10000.0)
    down_px = entry_ask * (1.0 - down_bps / 10000.0)
    t0 = ticks[i].ts
    max_bid = None
    min_bid = None
    last_bid = entry_bid
    events = 0
    result = "NEITHER"
    hit_time = None
    hit_sec = None
    data_complete = True

    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > horizon:
            break
        if t.session != ticks[i].session:
            result = "DATA_END"
            data_complete = False
            break
        bid = t.board.canonical_best_bid
        if bid is None or bid <= 0:
            continue
        events += 1
        last_bid = float(bid)
        max_bid = last_bid if max_bid is None else max(max_bid, last_bid)
        min_bid = last_bid if min_bid is None else min(min_bid, last_bid)
        up_hit = last_bid >= up_px
        down_hit = last_bid <= down_px
        if up_hit and down_hit:
            result = "BOTH_SAME_EVENT"
            hit_time = t.ts
            hit_sec = dt
            break
        if up_hit:
            result = "UP_FIRST"
            hit_time = t.ts
            hit_sec = dt
            break
        if down_hit:
            result = "DOWN_FIRST"
            hit_time = t.ts
            hit_sec = dt
            break
    else:
        # exhausted ticks before horizon
        if (ticks[-1].ts - t0).total_seconds() < horizon:
            result = "DATA_END"
            data_complete = False

    mfe = _bps(entry_ask, max_bid) if max_bid is not None else None
    mae = _bps(entry_ask, min_bid) if min_bid is not None else None
    term = _bps(entry_ask, last_bid) if last_bid else None
    cadj = (term - COST_BPS) if term is not None else None

    return LabelRow(
        sample_id=sample_id, barrier=barrier_id, entry_ask=entry_ask, entry_bid=entry_bid,
        entry_spread=spread_bps, up_barrier=up_px, down_barrier=down_px, horizon_sec=horizon,
        first_result=result, first_hit_time=hit_time, first_hit_sec=hit_sec,
        max_future_bid=max_bid, min_future_bid=min_bid, MFE_bps=mfe, MAE_bps=mae,
        terminal_return_bps=term, cost_adjusted_return_bps=cadj,
        events_observed=events, data_complete=data_complete,
    )


def label_summary(rows: list[LabelRow]) -> dict[str, Any]:
    n = len(rows) or 1
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.first_result] = counts.get(r.first_result, 0) + 1
    up = counts.get("UP_FIRST", 0)
    dn = counts.get("DOWN_FIRST", 0)
    mfe = [r.MFE_bps for r in rows if r.MFE_bps is not None]
    mae = [r.MAE_bps for r in rows if r.MAE_bps is not None]
    cadj = [r.cost_adjusted_return_bps for r in rows if r.cost_adjusted_return_bps is not None]
    abs_mae = [abs(x) for x in mae if x is not None]
    return {
        "n": len(rows),
        "UP_FIRST": up, "UP_FIRST_rate": up / n,
        "DOWN_FIRST": dn, "DOWN_FIRST_rate": dn / n,
        "NEITHER": counts.get("NEITHER", 0), "NEITHER_rate": counts.get("NEITHER", 0) / n,
        "BOTH_SAME_EVENT": counts.get("BOTH_SAME_EVENT", 0),
        "DATA_END": counts.get("DATA_END", 0),
        "STALE_BLOCKED": counts.get("STALE_BLOCKED", 0),
        "up_down_ratio": (up / dn) if dn > 0 else None,
        "avg_MFE_bps": sum(mfe) / len(mfe) if mfe else None,
        "avg_MAE_bps": sum(mae) / len(mae) if mae else None,
        "mfe_mae_ratio": (sum(mfe) / len(mfe)) / (sum(abs_mae) / len(abs_mae)) if mfe and abs_mae and sum(abs_mae) else None,
        "avg_cost_adj_bps": sum(cadj) / len(cadj) if cadj else None,
        "avg_first_hit_sec": (
            sum(r.first_hit_sec for r in rows if r.first_hit_sec is not None)
            / max(1, sum(1 for r in rows if r.first_hit_sec is not None))
        ),
    }

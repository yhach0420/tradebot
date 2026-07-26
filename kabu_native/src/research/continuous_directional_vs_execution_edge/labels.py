"""Directional (mid/bid/ask) and execution labels; mechanical DOWN audit."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.continuous_directional_vs_execution_edge.constants import COST_BPS, D_BARRIERS, EXEC_HORIZONS
from research.ueia_continuous_session_tradability_repair.session import continuous_session_id
from research.upward_edge_identification_audit.loader import Tick


def _bps(a: float, b: float) -> float:
    return (b - a) / a * 10000.0 if a and a > 0 else 0.0


def tick_size_jpy(px: float) -> float:
    """Simplified TSE cash tick (pre-2023/post common bands enough for audit)."""
    if px is None or px <= 0:
        return 1.0
    if px < 1000:
        return 0.1
    if px < 3000:
        return 1.0
    if px < 5000:
        return 5.0
    if px < 10000:
        return 10.0
    if px < 30000:
        return 10.0
    if px < 50000:
        return 50.0
    return 100.0


def quote_ok(bid: Optional[float], ask: Optional[float]) -> tuple[bool, str]:
    if bid is None or ask is None:
        return False, "missing"
    if bid <= 0 or ask <= 0:
        return False, "non_positive"
    if bid > ask:
        return False, "crossed"
    if bid == ask:
        return True, "locked"  # allowed but flagged
    return True, "ok"


@dataclass
class DirLabel:
    sample_id: str
    kind: str  # D-MID / D-BID / D-ASK
    barrier: str
    anchor: float
    up_barrier: float
    down_barrier: float
    horizon_sec: float
    first_result: str
    first_hit_sec: Optional[float]
    mfe_bps: Optional[float]
    mae_bps: Optional[float]
    terminal_bps: Optional[float]


@dataclass
class MechRow:
    sample_id: str
    barrier: str  # B2/B4
    entry_bid: float
    entry_ask: float
    entry_mid: float
    spread_bps: float
    spread_ticks: float
    down_barrier_bps: float
    spread_exceeds_down_barrier: bool
    first_future_bid: Optional[float]
    first_future_mid: Optional[float]
    first_bid_return_from_ask_bps: Optional[float]
    first_bid_return_from_bid_bps: Optional[float]
    first_mid_return_bps: Optional[float]
    first_result_original: str
    first_hit_sec: Optional[float]
    bid_price_changed: bool
    mid_price_changed: bool
    mechanical_down_strict: bool
    mechanical_down_bid: bool
    down_at_first_future_bid: bool


def first_passage_series(
    ticks: Sequence[Tick],
    i: int,
    anchor: float,
    get_px,  # callable(Tick) -> Optional[float]
    up_bps: float,
    down_bps: float,
    horizon: float,
) -> tuple[str, Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Returns result, hit_sec, mfe, mae, terminal, first_future_px."""
    up_px = anchor * (1.0 + up_bps / 10000.0)
    down_px = anchor * (1.0 - down_bps / 10000.0)
    t0 = ticks[i].ts
    sess0 = continuous_session_id(t0)
    max_px = min_px = None
    last = None
    first_fut = None
    hit_sec = None
    result = "NEITHER"
    if sess0 is None or anchor <= 0:
        return "STALE_BLOCKED", None, None, None, None, None

    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        dt = (t.ts - t0).total_seconds()
        if dt > horizon:
            break
        if continuous_session_id(t.ts) != sess0:
            result = "DATA_END_SESSION_BOUNDARY"
            break
        px = get_px(t)
        if px is None or px <= 0:
            continue
        if first_fut is None:
            first_fut = px
        last = px
        max_px = px if max_px is None else max(max_px, px)
        min_px = px if min_px is None else min(min_px, px)
        up_hit = px >= up_px
        dn_hit = px <= down_px
        if up_hit and dn_hit:
            result, hit_sec = "BOTH_SAME_EVENT", dt
            break
        if up_hit:
            result, hit_sec = "UP_FIRST", dt
            break
        if dn_hit:
            result, hit_sec = "DOWN_FIRST", dt
            break

    mfe = _bps(anchor, max_px) if max_px is not None else None
    mae = _bps(anchor, min_px) if min_px is not None else None
    term = _bps(anchor, last) if last is not None else None
    return result, hit_sec, mfe, mae, term, first_fut


def mid_of(t: Tick) -> Optional[float]:
    b, a = t.board.canonical_best_bid, t.board.canonical_best_ask
    if b and a and b > 0 and a > 0:
        return (float(b) + float(a)) / 2.0
    return None


def make_directional_labels(ticks: Sequence[Tick], i: int, sample_id: str, entry_bid: float, entry_ask: float) -> dict[str, DirLabel]:
    out = {}
    mid0 = (entry_bid + entry_ask) / 2.0
    for bid, spec in D_BARRIERS.items():
        for kind, anchor, getter in (
            ("D-MID", mid0, mid_of),
            ("D-BID", entry_bid, lambda t: float(t.board.canonical_best_bid) if t.board.canonical_best_bid else None),
            ("D-ASK", entry_ask, lambda t: float(t.board.canonical_best_ask) if t.board.canonical_best_ask else None),
        ):
            res, hit, mfe, mae, term, _ = first_passage_series(
                ticks, i, anchor, getter, spec["up_bps"], spec["down_bps"], spec["horizon_sec"],
            )
            key = f"{kind}_{bid}"
            out[key] = DirLabel(
                sample_id=sample_id, kind=kind, barrier=bid, anchor=anchor,
                up_barrier=anchor * (1 + spec["up_bps"] / 10000),
                down_barrier=anchor * (1 - spec["down_bps"] / 10000),
                horizon_sec=spec["horizon_sec"], first_result=res, first_hit_sec=hit,
                mfe_bps=mfe, mae_bps=mae, terminal_bps=term,
            )
    return out


def mechanical_down_audit(
    ticks: Sequence[Tick],
    i: int,
    sample_id: str,
    entry_bid: float,
    entry_ask: float,
    spread_bps: float,
    orig_result: str,
    orig_hit_sec: Optional[float],
    barrier_name: str,
    down_barrier_bps: float,
) -> MechRow:
    mid0 = (entry_bid + entry_ask) / 2.0
    tick = tick_size_jpy(entry_ask)
    spread_ticks = (entry_ask - entry_bid) / tick if tick > 0 else 0.0
    exceeds = spread_bps >= down_barrier_bps

    # walk until original barrier hit or horizon proxy
    first_bid = first_mid = None
    bid_changed = False
    mid_changed = False
    down_px = entry_ask * (1.0 - down_barrier_bps / 10000.0)
    t0 = ticks[i].ts
    sess0 = continuous_session_id(t0)
    hit_bid_at = None
    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess0:
            break
        b = t.board.canonical_best_bid
        a = t.board.canonical_best_ask
        if b is None or b <= 0:
            continue
        bf = float(b)
        mf = (bf + float(a)) / 2.0 if a and a > 0 else None
        if first_bid is None:
            first_bid = bf
            first_mid = mf
        if abs(bf - entry_bid) > 1e-9:
            bid_changed = True
        if mf is not None and abs(mf - mid0) > 1e-9:
            mid_changed = True
        if bf <= down_px and hit_bid_at is None:
            hit_bid_at = (t.ts - t0).total_seconds()
            # freeze change flags at hit for mechanical definition
            break
        if orig_hit_sec is not None and (t.ts - t0).total_seconds() >= orig_hit_sec:
            break

    down_first_orig = orig_result == "DOWN_FIRST"
    down_at_first = bool(first_bid is not None and first_bid <= down_px)
    md_strict = bool(down_first_orig and exceeds and (not bid_changed) and (not mid_changed))
    md_bid = bool(down_first_orig and exceeds and (not bid_changed))

    return MechRow(
        sample_id=sample_id, barrier=barrier_name,
        entry_bid=entry_bid, entry_ask=entry_ask, entry_mid=mid0,
        spread_bps=spread_bps, spread_ticks=spread_ticks,
        down_barrier_bps=down_barrier_bps,
        spread_exceeds_down_barrier=exceeds,
        first_future_bid=first_bid, first_future_mid=first_mid,
        first_bid_return_from_ask_bps=_bps(entry_ask, first_bid) if first_bid else None,
        first_bid_return_from_bid_bps=_bps(entry_bid, first_bid) if first_bid else None,
        first_mid_return_bps=_bps(mid0, first_mid) if first_mid else None,
        first_result_original=orig_result, first_hit_sec=orig_hit_sec,
        bid_price_changed=bid_changed, mid_price_changed=mid_changed,
        mechanical_down_strict=md_strict, mechanical_down_bid=md_bid,
        down_at_first_future_bid=down_at_first,
    )


def execution_horizons(
    ticks: Sequence[Tick],
    i: int,
    entry_ask: float,
) -> dict[str, Any]:
    """Fixed-horizon ask→bid returns with single 5bps deduction."""
    t0 = ticks[i].ts
    sess0 = continuous_session_id(t0)
    out = {}
    for h in EXEC_HORIZONS:
        max_bid = min_bid = None
        last = None
        for j in range(i + 1, len(ticks)):
            t = ticks[j]
            dt = (t.ts - t0).total_seconds()
            if dt > h:
                break
            if continuous_session_id(t.ts) != sess0:
                break
            b = t.board.canonical_best_bid
            if b is None or b <= 0:
                continue
            bf = float(b)
            last = bf
            max_bid = bf if max_bid is None else max(max_bid, bf)
            min_bid = bf if min_bid is None else min(min_bid, bf)
        term = _bps(entry_ask, last) if last else None
        mfe = _bps(entry_ask, max_bid) if max_bid else None
        mae = _bps(entry_ask, min_bid) if min_bid else None
        cadj = (term - COST_BPS) if term is not None else None
        out[f"h{int(h)}"] = {
            "terminal_bps": term, "cost_adj_bps": cadj, "mfe_bps": mfe, "mae_bps": mae,
            "yen_100": (cadj / 10000.0 * entry_ask * 100) if cadj is not None else None,
        }
    return out

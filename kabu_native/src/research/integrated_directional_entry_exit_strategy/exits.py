"""X1–X5 EXIT simulation from ENTRY ask / bid path."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from research.integrated_directional_entry_exit_strategy.constants import COST_RATE, FIXED_THRESHOLD, LOT
from research.integrated_directional_entry_exit_strategy.entries import EntryHit
from research.integrated_directional_entry_exit_strategy.market import (
    bid,
    bps_from_entry,
    flow_stats,
    mid,
    min_mid_window,
)
from research.ueia_continuous_session_tradability_repair.session import (
    continuous_session_id,
    session_end_time,
)
from research.upward_edge_identification_audit.loader import Tick


@dataclass
class TradeResult:
    day: str
    symbol: str
    sample_id: str
    strategy_id: str
    entry_arm: str
    exit_arm: str
    entry_time: datetime
    exit_time: datetime
    entry_ask: float
    exit_bid: float
    signal_time: datetime
    signal_score: float
    entry_spread_bps: float
    confirm_wait_sec: float
    hold_sec: float
    exit_reason: str
    net_pnl_yen_100: float
    net_return_bps: float
    mfe_bps: float
    mae_bps: float
    pnl_5s: Optional[float]
    pnl_30s: Optional[float]
    pnl_180s: Optional[float]
    episode_id: str


def _econ(entry: float, exit_px: float) -> tuple[float, float]:
    gross = (exit_px - entry) * LOT
    cost = entry * LOT * COST_RATE
    net = gross - cost
    bps = net / (entry * LOT) * 10000.0 if entry > 0 else 0.0
    return net, bps


def _bid_at_horizon(ticks: Sequence[Tick], i: int, t0: datetime, horizon: float, sess: str) -> Optional[float]:
    target = t0 + timedelta(seconds=horizon)
    last = None
    for j in range(i, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            break
        b = bid(t)
        if b is not None:
            last = b
        if t.ts >= target and b is not None:
            return b
    return last


def simulate_exit(
    hit: EntryHit,
    exit_arm: str,
    ticks: Sequence[Tick],
    *,
    future_scores: Sequence[tuple[datetime, float]] | None = None,
) -> Optional[TradeResult]:
    i = hit.entry_idx
    entry = hit.entry_ask
    t0 = hit.entry_time
    sess = continuous_session_id(t0)
    if sess is None:
        return None
    sess_end = session_end_time(t0)
    max_hold = {
        "X1": 180.0, "X2": 180.0, "X3": 300.0, "X4": 180.0, "X5": 300.0,
    }[exit_arm]
    deadline = t0 + timedelta(seconds=max_hold)
    if sess_end and sess_end < deadline:
        # can hold until session end
        pass

    max_bid = min_bid = None
    max_mfe = 0.0  # in bps from entry ask using bid
    trail_active = False
    trail_peak = None  # peak bid bps
    exit_px = exit_ts = exit_reason = None
    pnl_5s = pnl_30s = pnl_180s = None
    scored = list(future_scores or [])
    score_ptr = 0

    for j in range(i, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            # session boundary — close at last bid if any, else current
            if max_bid is not None and exit_px is None:
                # find last bid before boundary
                for k in range(j - 1, i - 1, -1):
                    bb = bid(ticks[k])
                    if bb is not None:
                        exit_px, exit_ts, exit_reason = bb, ticks[k].ts, "SESSION_CLOSE"
                        break
            break
        b = bid(t)
        if b is None:
            continue
        ret = bps_from_entry(entry, b)
        max_bid = b if max_bid is None else max(max_bid, b)
        min_bid = b if min_bid is None else min(min_bid, b)
        max_mfe = max(max_mfe, bps_from_entry(entry, max_bid))
        dt = (t.ts - t0).total_seconds()
        if pnl_5s is None and dt >= 5.0:
            pnl_5s = _econ(entry, b)[0]
        if pnl_30s is None and dt >= 30.0:
            pnl_30s = _econ(entry, b)[0]
        if pnl_180s is None and dt >= 180.0:
            pnl_180s = _econ(entry, b)[0]

        # hard session end
        if sess_end and t.ts >= sess_end:
            exit_px, exit_ts, exit_reason = b, t.ts, "SESSION_CLOSE"
            break

        if exit_arm == "X1":
            if dt >= 180.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "FIXED_180"
                break

        elif exit_arm == "X2":
            if ret >= 30.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "TARGET"
                break
            if ret <= -15.0 + 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "STOP"
                break
            if dt >= 180.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "MAX_HOLD"
                break

        elif exit_arm == "X3":
            if ret <= -15.0 + 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "STOP"
                break
            if max_mfe >= 20.0 - 1e-9:
                trail_active = True
            if trail_active:
                # giveback 50% of max MFE from peak
                peak_bps = max_mfe
                giveback_floor = peak_bps * 0.50
                if ret <= giveback_floor + 1e-9:
                    exit_px, exit_ts, exit_reason = b, t.ts, "TRAILING"
                    break
            if dt >= 300.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "MAX_HOLD"
                break

        elif exit_arm == "X4":
            if ret <= -15.0 + 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "STOP"
                break
            if dt >= 5.0 - 1e-9:
                fs = flow_stats(ticks, j, 5.0, t_ref=t.ts)
                br = fs.get("buy_ratio")
                flow_exit = False
                reason = None
                if br is not None and br < 0.45 - 1e-12:
                    flow_exit, reason = True, "FLOW_DECAY_BUY_RATIO"
                elif fs["sell_q"] > fs["buy_q"]:
                    flow_exit, reason = True, "FLOW_DECAY_SELL_Q"
                else:
                    m = mid(t)
                    mn = min_mid_window(ticks, j, t.ts, 10.0)
                    if m is not None and mn is not None and m <= mn + 1e-12:
                        # updating 10s low: mid equals window min (at a new low)
                        # stricter: mid <= previous window min before this tick
                        prev_min = min_mid_window(ticks, j, t.ts - timedelta(microseconds=1), 10.0)
                        if prev_min is not None and m < prev_min - 1e-12:
                            flow_exit, reason = True, "FLOW_DECAY_MID_LOW"
                while score_ptr < len(scored) and scored[score_ptr][0] <= t.ts:
                    if scored[score_ptr][1] < FIXED_THRESHOLD:
                        flow_exit, reason = True, "FLOW_DECAY_SCORE"
                    score_ptr += 1
                if flow_exit:
                    exit_px, exit_ts, exit_reason = b, t.ts, reason or "FLOW_DECAY"
                    break
            if dt >= 180.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "MAX_HOLD"
                break

        elif exit_arm == "X5":
            if ret <= -15.0 + 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "STOP"
                break
            if ret >= 50.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "TARGET"
                break
            if max_mfe >= 20.0 - 1e-9:
                trail_active = True
            if trail_active:
                # giveback 40% of max MFE
                floor = max_mfe * (1.0 - 0.40)
                if ret <= floor + 1e-9:
                    exit_px, exit_ts, exit_reason = b, t.ts, "TRAILING"
                    break
            if dt >= 300.0 - 1e-9:
                exit_px, exit_ts, exit_reason = b, t.ts, "MAX_HOLD"
                break

    if exit_px is None:
        # data end — use last bid
        for k in range(len(ticks) - 1, i - 1, -1):
            if continuous_session_id(ticks[k].ts) != sess:
                continue
            bb = bid(ticks[k])
            if bb is not None:
                exit_px, exit_ts, exit_reason = bb, ticks[k].ts, "DATA_END"
                break
    if exit_px is None or exit_ts is None:
        return None

    net, nbps = _econ(entry, exit_px)
    mfe = bps_from_entry(entry, max_bid) if max_bid is not None else 0.0
    mae = bps_from_entry(entry, min_bid) if min_bid is not None else 0.0
    # fill diagnostic horizons if missing
    if pnl_5s is None:
        bx = _bid_at_horizon(ticks, i, t0, 5.0, sess)
        if bx is not None:
            pnl_5s = _econ(entry, bx)[0]
    if pnl_30s is None:
        bx = _bid_at_horizon(ticks, i, t0, 30.0, sess)
        if bx is not None:
            pnl_30s = _econ(entry, bx)[0]
    if pnl_180s is None:
        bx = _bid_at_horizon(ticks, i, t0, 180.0, sess)
        if bx is not None:
            pnl_180s = _econ(entry, bx)[0]

    return TradeResult(
        day=hit.sample.day, symbol=hit.sample.symbol, sample_id=hit.sample.sample_id,
        strategy_id=f"{hit.entry_arm}_{exit_arm}", entry_arm=hit.entry_arm, exit_arm=exit_arm,
        entry_time=t0, exit_time=exit_ts, entry_ask=entry, exit_bid=exit_px,
        signal_time=hit.signal_time, signal_score=hit.signal_score,
        entry_spread_bps=hit.entry_spread_bps, confirm_wait_sec=hit.confirm_wait_sec,
        hold_sec=(exit_ts - t0).total_seconds(), exit_reason=exit_reason or "UNKNOWN",
        net_pnl_yen_100=net, net_return_bps=nbps, mfe_bps=mfe, mae_bps=mae,
        pnl_5s=pnl_5s, pnl_30s=pnl_30s, pnl_180s=pnl_180s,
        episode_id=f"{hit.sample.sample_id}|{hit.entry_arm}|{exit_arm}",
    )

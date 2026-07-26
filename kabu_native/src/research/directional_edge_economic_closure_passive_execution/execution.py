"""Immediate-cross and queue-aware passive entry arms."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.continuous_directional_vs_execution_edge.labels import tick_size_jpy
from research.directional_edge_economic_closure_passive_execution.constants import (
    COST_RATE,
    LIMIT_TIMEOUT_SEC,
    LOT,
    PRIMARY_HORIZON_SEC,
)
from research.directional_edge_economic_closure_passive_execution.economics import net_pnl_yen_100
from research.ueia_continuous_session_tradability_repair.session import continuous_session_id
from research.upward_edge_identification_audit.loader import Tick


def _bid(t: Tick) -> Optional[float]:
    b = t.board.canonical_best_bid
    return float(b) if b and b > 0 else None


def _ask(t: Tick) -> Optional[float]:
    a = t.board.canonical_best_ask
    return float(a) if a and a > 0 else None


def _bid_qty(t: Tick) -> float:
    q = t.board.canonical_bid_qty
    return float(q) if q is not None else 0.0


def _ask_qty(t: Tick) -> float:
    q = t.board.canonical_ask_qty
    return float(q) if q is not None else 0.0


def exit_bid_at(ticks: Sequence[Tick], i: int, signal_ts: datetime, horizon_sec: float) -> tuple[Optional[float], Optional[datetime], str]:
    """EXIT at signal_time + horizon (same session)."""
    target = signal_ts + timedelta(seconds=horizon_sec)
    sess0 = continuous_session_id(signal_ts)
    last_bid = None
    last_ts = None
    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess0:
            return last_bid, last_ts, "DATA_END_SESSION_BOUNDARY"
        b = _bid(t)
        if b is not None:
            last_bid, last_ts = b, t.ts
        if t.ts >= target and b is not None:
            return b, t.ts, "OK"
    if last_bid is not None:
        return last_bid, last_ts, "DATA_END"
    return None, None, "STALE_BLOCKED"


def immediate_cross_trade(sample, ticks: Sequence[Tick], horizon_sec: float = PRIMARY_HORIZON_SEC) -> dict[str, Any]:
    i = sample.idx
    entry = float(sample.entry_ask)
    t0 = sample.event_time
    exit_px, exit_ts, status = exit_bid_at(ticks, i, t0, horizon_sec)
    if exit_px is None:
        return {
            "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
            "status": status, "filled_qty": 0, "qty": 0, "net_pnl_yen_100": 0.0,
            "net_return_bps": 0.0, "entry_price": entry, "exit_price": None,
            "entry_notional_yen": 0.0, "entry_time": t0, "exit_time": None,
            "arm": "E0", "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
        }
    econ = net_pnl_yen_100(entry, exit_px, LOT)
    # MFE/MAE on bid path until exit
    mfe = mae = None
    sess0 = continuous_session_id(t0)
    max_b = min_b = None
    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess0:
            break
        b = _bid(t)
        if b is None:
            continue
        max_b = b if max_b is None else max(max_b, b)
        min_b = b if min_b is None else min(min_b, b)
        if exit_ts and t.ts >= exit_ts:
            break
    if max_b is not None:
        mfe = (max_b - entry) / entry * 10000.0
        mae = (min_b - entry) / entry * 10000.0
    return {
        "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
        "status": "FULL_FILL", "filled_qty": LOT, "qty": LOT,
        "entry_price": entry, "exit_price": exit_px,
        "entry_time": t0, "exit_time": exit_ts, "arm": "E0",
        "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
        "mfe_bps": mfe, "mae_bps": mae, **econ,
    }


def _limit_price(arm: str, bid: float, ask: float) -> Optional[float]:
    tick = tick_size_jpy(ask)
    if arm == "E2":
        return bid
    if arm == "E3":
        cand = bid + tick
        return bid if cand >= ask - 1e-12 else cand
    if arm == "E4":
        # highest tick <= mid and > bid
        mid = (bid + ask) / 2.0
        # ticks strictly inside (bid, ask)
        p = bid + tick
        best = None
        while p < ask - 1e-12:
            if p <= mid + 1e-12:
                best = p
            p += tick
        return best if best is not None else bid
    return None


@dataclass
class QueueState:
    order_price: float
    order_qty: float
    queue_ahead: float
    filled: float = 0.0
    first_fill: Optional[datetime] = None
    last_fill: Optional[datetime] = None
    avg_px_num: float = 0.0

    def add_fill(self, qty: float, px: float, ts: datetime) -> None:
        qty = min(qty, self.order_qty - self.filled)
        if qty <= 0:
            return
        self.avg_px_num += px * qty
        self.filled += qty
        if self.first_fill is None:
            self.first_fill = ts
        self.last_fill = ts

    @property
    def avg_fill_price(self) -> Optional[float]:
        return self.avg_px_num / self.filled if self.filled > 0 else None


def simulate_passive_arm(
    sample,
    ticks: Sequence[Tick],
    arm: str,
    *,
    horizon_sec: float = PRIMARY_HORIZON_SEC,
    timeout_sec: float = LIMIT_TIMEOUT_SEC,
    moderate_queue: bool = False,
) -> dict[str, Any]:
    """CONSERVATIVE_QUEUE by default. E1 is wait-then-cross."""
    i = sample.idx
    t0 = sample.event_time
    sess0 = continuous_session_id(t0)
    expire = t0 + timedelta(seconds=timeout_sec)
    exit_deadline = t0 + timedelta(seconds=horizon_sec)
    bid0 = float(sample.entry_bid)
    ask0 = float(sample.entry_ask)

    # E1: wait for spread compress then cross ask
    if arm == "E1":
        entry_px = None
        entry_ts = None
        entry_idx = None
        for j in range(i, len(ticks)):
            t = ticks[j]
            if continuous_session_id(t.ts) != sess0:
                break
            if t.ts > expire:
                break
            b, a = _bid(t), _ask(t)
            if b is None or a is None:
                continue
            spr = (a - b) / a * 10000.0
            if spr <= 5.0 + 1e-9:
                entry_px, entry_ts, entry_idx = a, t.ts, j
                break
        if entry_px is None:
            return _no_fill(sample, arm, "NO_FILL")
        exit_px, exit_ts, st = exit_bid_at(ticks, entry_idx, t0, horizon_sec)  # signal+horizon
        if exit_px is None:
            return _no_fill(sample, arm, st)
        econ = net_pnl_yen_100(entry_px, exit_px, LOT)
        return {
            "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
            "status": "FULL_FILL", "filled_qty": LOT, "qty": LOT, "arm": arm,
            "entry_price": entry_px, "exit_price": exit_px, "entry_time": entry_ts, "exit_time": exit_ts,
            "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
            "fill_latency_ms": (entry_ts - t0).total_seconds() * 1000.0, **econ,
        }

    # E2/E3/E4: post limit
    order_px = _limit_price(arm, bid0, ask0)
    if order_px is None:
        return _no_fill(sample, arm, "NO_FILL")
    # initial queue
    if abs(order_px - bid0) < 1e-9:
        queue_ahead = _bid_qty(ticks[i])
    else:
        queue_ahead = 0.0
    qs = QueueState(order_price=order_px, order_qty=float(LOT), queue_ahead=queue_ahead)
    prev_bid_qty = _bid_qty(ticks[i])

    for j in range(i + 1, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess0:
            break
        if t.ts > expire and qs.filled <= 0:
            break
        if t.ts > expire and qs.filled > 0:
            break  # stop new fills after timeout; keep partial
        ask = _ask(t)
        bid = _bid(t)
        # marketable if ask <= order
        if ask is not None and ask <= order_px + 1e-12:
            aq = _ask_qty(t)
            # fill against ask size at ask price
            fill_q = min(aq if aq > 0 else float(LOT), qs.order_qty - qs.filled)
            if fill_q > 0:
                qs.add_fill(fill_q, ask, t.ts)
            if qs.filled >= qs.order_qty - 1e-9:
                break
            continue
        # sell-aggressive trades at/below order price reduce queue
        if t.trade_side == "SELL" and t.volume_delta and t.volume_delta > 0 and t.px is not None:
            if float(t.px) <= order_px + 1e-12:
                qty = float(t.volume_delta)
                if qs.queue_ahead > 0:
                    eat = min(qs.queue_ahead, qty)
                    qs.queue_ahead -= eat
                    qty -= eat
                if qty > 0:
                    qs.add_fill(qty, order_px, t.ts)
        elif moderate_queue and bid is not None and abs(bid - order_px) < 1e-9:
            bq = _bid_qty(t)
            if prev_bid_qty > bq:
                # 50% of unexplained decrease as cancel
                dec = prev_bid_qty - bq
                # subtract traded if sell at price
                traded = float(t.volume_delta) if (t.trade_side == "SELL" and t.volume_delta) else 0.0
                cancel_proxy = max(0.0, dec - traded) * 0.5
                qs.queue_ahead = max(0.0, qs.queue_ahead - cancel_proxy)
            prev_bid_qty = bq
        if bid is not None and abs(bid - order_px) < 1e-9:
            prev_bid_qty = _bid_qty(t)
        if qs.filled >= qs.order_qty - 1e-9:
            break

    if qs.filled <= 0:
        return _no_fill(sample, arm, "NO_FILL", queue_ahead=queue_ahead, order_price=order_px)

    entry_px = qs.avg_fill_price or order_px
    filled = qs.filled
    status = "FULL_FILL" if filled >= LOT - 1e-9 else "PARTIAL_FILL"
    exit_px, exit_ts, st = exit_bid_at(ticks, i, t0, horizon_sec)
    if exit_px is None:
        return {
            "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
            "status": st, "filled_qty": filled, "qty": filled, "arm": arm,
            "entry_price": entry_px, "exit_price": None, "net_pnl_yen_100": 0.0,
            "net_return_bps": 0.0, "entry_notional_yen": entry_px * filled,
            "entry_time": qs.first_fill, "exit_time": None,
            "order_price": order_px, "initial_queue_ahead": queue_ahead,
            "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
        }
    econ = net_pnl_yen_100(entry_px, exit_px, int(round(filled)))
    # scale if filled != 100 due to int round — use exact filled
    if abs(filled - LOT) > 1e-6:
        gross = (exit_px - entry_px) * filled
        cost = entry_px * filled * COST_RATE
        net = gross - cost
        notional = entry_px * filled
        econ = {
            "qty": filled, "gross_pnl_yen": gross, "cost_yen": cost, "net_pnl_yen_100": net,
            "gross_return_bps": (exit_px - entry_px) / entry_px * 10000.0,
            "net_return_bps": net / notional * 10000.0 if notional else 0.0,
            "entry_notional_yen": notional, "return_on_notional": net / notional if notional else 0.0,
        }
    return {
        "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
        "status": status, "filled_qty": filled, "unfilled_qty": LOT - filled,
        "qty": filled, "arm": arm, "order_price": order_px,
        "initial_queue_ahead": queue_ahead,
        "entry_price": entry_px, "exit_price": exit_px,
        "entry_time": qs.first_fill, "exit_time": exit_ts,
        "first_fill_time": qs.first_fill, "last_fill_time": qs.last_fill,
        "fill_latency_ms": ((qs.first_fill - t0).total_seconds() * 1000.0) if qs.first_fill else None,
        "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
        **econ,
    }


def _no_fill(sample, arm: str, status: str, **extra) -> dict[str, Any]:
    return {
        "day": sample.day, "symbol": sample.symbol, "sample_id": sample.sample_id,
        "status": status, "filled_qty": 0, "qty": 0, "arm": arm,
        "entry_price": float(sample.entry_ask), "exit_price": None,
        "net_pnl_yen_100": 0.0, "net_return_bps": 0.0, "entry_notional_yen": 0.0,
        "gross_pnl_yen": 0.0, "cost_yen": 0.0, "gross_return_bps": 0.0,
        "return_on_notional": 0.0,
        "entry_time": sample.event_time, "exit_time": None,
        "spread_bps": getattr(sample, "_spread_bps", sample.spread_bps),
        **extra,
    }

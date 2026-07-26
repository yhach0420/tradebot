"""Evaluation sample generation — regular + state-change; features stride=1."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from research.upward_edge_identification_audit.constants import (
    BARRIERS,
    MAX_REGULAR_PER_STREAM,
    MAX_STATE_PER_STREAM,
    REGULAR_SAMPLE_SEC,
    STATE_SAMPLE_MIN_GAP_SEC,
)
from research.upward_edge_identification_audit.features import FeatureEngine
from research.upward_edge_identification_audit.labels import LabelRow, label_first_passage
from research.upward_edge_identification_audit.loader import Tick, exec_entry_ok


@dataclass
class Sample:
    sample_id: str
    day: str
    symbol: str
    event_sequence: int
    event_time: datetime
    sample_type: str
    idx: int
    entry_ask: float
    entry_bid: float
    spread_bps: Optional[float]
    features: dict[str, Optional[float]] = field(default_factory=dict)
    labels: dict[str, LabelRow] = field(default_factory=dict)
    stream_key: str = ""


def _state_changed(prev: Tick, cur: Tick) -> bool:
    if prev.board.canonical_best_bid != cur.board.canonical_best_bid:
        return True
    if prev.board.canonical_best_ask != cur.board.canonical_best_ask:
        return True
    ps = prev.board.canonical_spread_bps
    cs = cur.board.canonical_spread_bps
    if ps is not None and cs is not None and abs(ps - cs) >= 1.0:
        return True
    if prev.trade_side in ("BUY", "SELL") and cur.trade_side in ("BUY", "SELL") and prev.trade_side != cur.trade_side:
        return True
    return False


def build_day_context(streams: dict[str, list[Tick]], day: str) -> dict[str, Any]:
    """Cross-sectional Watch50 returns/flow at coarse timestamps for G5."""
    # Build per-symbol last mid and flow; compute snapshots every 10s wall clock
    by_sym = {k.split("|", 1)[1]: v for k, v in streams.items() if k.startswith(day + "|")}
    if not by_sym:
        return {"timeline": []}
    # Collect union of times rounded to 10s
    pointers = {s: 0 for s in by_sym}
    # Precompute sparse timeline from first symbol densest
    times = []
    for ticks in by_sym.values():
        if ticks:
            t0 = ticks[0].ts
            t1 = ticks[-1].ts
            cur = t0
            from datetime import timedelta
            while cur <= t1:
                times.append(cur)
                cur = cur + timedelta(seconds=10)
            break
    timeline = []
    for ts in times:
        rets = []
        buy_b = sell_b = 0
        n = 0
        for sym, ticks in by_sym.items():
            # advance pointer
            i = pointers[sym]
            while i + 1 < len(ticks) and ticks[i + 1].ts <= ts:
                i += 1
            pointers[sym] = i
            if not ticks:
                continue
            t = ticks[i]
            bid, ask = t.board.canonical_best_bid, t.board.canonical_best_ask
            if not bid or not ask:
                continue
            mid = (bid + ask) / 2
            # 30s lookback mid
            j = i
            while j > 0 and (ts - ticks[j].ts).total_seconds() < 30:
                j -= 1
            mid0 = None
            b0, a0 = ticks[j].board.canonical_best_bid, ticks[j].board.canonical_best_ask
            if b0 and a0:
                mid0 = (b0 + a0) / 2
            if mid0 and mid0 > 0:
                rets.append((sym, (mid - mid0) / mid0))
            # flow 10s
            buy = sell = 0.0
            k = i
            while k >= 0 and (ts - ticks[k].ts).total_seconds() <= 10:
                if ticks[k].volume_delta and ticks[k].volume_delta > 0:
                    if ticks[k].trade_side == "BUY":
                        buy += ticks[k].volume_delta
                    elif ticks[k].trade_side == "SELL":
                        sell += ticks[k].volume_delta
                k -= 1
            n += 1
            if buy > sell:
                buy_b += 1
            elif sell > buy:
                sell_b += 1
        if not rets:
            continue
        vals = [r for _, r in rets]
        vals_sorted = sorted(vals)
        med = vals_sorted[len(vals_sorted) // 2]
        up_r = sum(1 for v in vals if v > 0) / len(vals)
        dn_r = sum(1 for v in vals if v < 0) / len(vals)
        timeline.append({
            "ts": ts,
            "median": med,
            "up_ratio": up_r,
            "down_ratio": dn_r,
            "buy_breadth": buy_b / n if n else None,
            "sell_breadth": sell_b / n if n else None,
            "rets": {s: r for s, r in rets},
        })
    return {"timeline": timeline}


def _ctx_at(ctx: dict, ts: datetime, symbol: str) -> dict[str, Optional[float]]:
    tl = ctx.get("timeline") or []
    if not tl:
        return {}
    # last snapshot <= ts
    best = None
    for row in tl:
        if row["ts"] <= ts:
            best = row
        else:
            break
    if best is None:
        return {}
    rets = best.get("rets") or {}
    r = rets.get(symbol)
    vals = sorted(rets.values())
    pct = None
    if r is not None and vals:
        pct = sum(1 for v in vals if v <= r) / len(vals)
    rank = pct
    return {
        "breadth_up": best.get("up_ratio"),
        "breadth_down": best.get("down_ratio"),
        "median_ret": best.get("median"),
        "rel_ret": (r - best["median"]) if r is not None and best.get("median") is not None else None,
        "ret_percentile": pct,
        "flow_percentile": pct,
        "rank_strength": rank,
        "mkt_buy_breadth": best.get("buy_breadth"),
        "mkt_sell_breadth": best.get("sell_breadth"),
    }


def build_samples_for_stream(
    stream_key: str,
    ticks: list[Tick],
    day_ctx: dict,
) -> list[Sample]:
    if len(ticks) < 50:
        return []
    day, symbol = stream_key.split("|", 1)
    eng = FeatureEngine()
    out: list[Sample] = []
    n_reg = n_state = 0
    last_reg_ts: Optional[datetime] = None
    last_state_ts: Optional[datetime] = None
    prev: Optional[Tick] = None

    for i, t in enumerate(ticks):
        eng.update(t)
        # inject market context
        c = _ctx_at(day_ctx, t.ts, symbol)
        for k, v in c.items():
            setattr(eng, k, v)

        if not eng.warmed(t) or not exec_entry_ok(t):
            prev = t
            continue
        ask = float(t.board.canonical_best_ask)
        bid = float(t.board.canonical_best_bid)
        spr = t.board.canonical_spread_bps

        take_reg = False
        take_state = False
        if last_reg_ts is None or (t.ts - last_reg_ts).total_seconds() >= REGULAR_SAMPLE_SEC:
            if n_reg < MAX_REGULAR_PER_STREAM:
                take_reg = True
        if prev is not None and _state_changed(prev, t):
            if last_state_ts is None or (t.ts - last_state_ts).total_seconds() >= STATE_SAMPLE_MIN_GAP_SEC:
                if n_state < MAX_STATE_PER_STREAM:
                    take_state = True

        if not take_reg and not take_state:
            prev = t
            continue

        feats = eng.snapshot(t)
        types = []
        if take_reg:
            types.append("REGULAR")
            n_reg += 1
            last_reg_ts = t.ts
        if take_state:
            types.append("STATE_CHANGE")
            n_state += 1
            last_state_ts = t.ts

        for st in types:
            sid = f"{day}|{symbol}|{t.event_seq}|{i}|{st}"
            sm = Sample(
                sample_id=sid, day=day, symbol=symbol, event_sequence=t.event_seq,
                event_time=t.ts, sample_type=st, idx=i, entry_ask=ask, entry_bid=bid,
                spread_bps=spr, features=feats, stream_key=stream_key,
            )
            for bid_id in BARRIERS:
                sm.labels[bid_id] = label_first_passage(
                    ticks, i, sid, bid_id, ask, bid, spr,
                )
            out.append(sm)
        prev = t
    return out


def build_all_samples(streams: dict[str, list[Tick]]) -> tuple[list[Sample], dict[str, Any]]:
    by_day: dict[str, dict[str, list[Tick]]] = defaultdict(dict)
    for key, ticks in streams.items():
        day, sym = key.split("|", 1)
        by_day[day][key] = ticks
    samples: list[Sample] = []
    meta = {"days": {}, "push_events": 0}
    for day, smap in sorted(by_day.items()):
        day_streams = smap
        n_events = sum(len(v) for v in day_streams.values())
        meta["push_events"] += n_events
        meta["days"][day] = {"symbols": len(day_streams), "events": n_events}
        print(f"[ueia] day_ctx {day} symbols={len(day_streams)}", flush=True)
        ctx = build_day_context(day_streams, day)
        for key, ticks in day_streams.items():
            samples.extend(build_samples_for_stream(key, ticks, ctx))
        print(f"[ueia] day {day} samples_so_far={len(samples)}", flush=True)
    return samples, meta

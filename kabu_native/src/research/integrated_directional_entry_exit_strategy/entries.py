"""E1–E4 ENTRY resolution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.integrated_directional_entry_exit_strategy.constants import CONFIRM_SEC, FIXED_THRESHOLD
from research.integrated_directional_entry_exit_strategy.market import (
    ask,
    bid,
    flow_stats,
    max_mid_before,
    mid,
    spread_bps,
    tick_size,
)
from research.ueia_continuous_session_tradability_repair.session import continuous_session_id
from research.upward_edge_identification_audit.loader import Tick
from research.upward_edge_identification_audit.samples import Sample


@dataclass
class EntryHit:
    sample: Sample
    entry_idx: int
    entry_time: datetime
    entry_ask: float
    entry_bid: float
    signal_time: datetime
    signal_score: float
    signal_spread_bps: float
    entry_spread_bps: float
    confirm_wait_sec: float
    entry_arm: str
    signal_mid: float


def _spr(s: Sample) -> float:
    if getattr(s, "_spread_bps", None) is not None:
        return float(s._spread_bps)
    if s.spread_bps is not None:
        return float(s.spread_bps)
    return (s.entry_ask - s.entry_bid) / s.entry_ask * 10000.0


def resolve_e1(s: Sample, score: float, ticks: Sequence[Tick]) -> Optional[EntryHit]:
    if score < FIXED_THRESHOLD:
        return None
    spr = _spr(s)
    if spr > 5.0 + 1e-9:
        return None
    i = s.idx
    a, b = ask(ticks[i]), bid(ticks[i])
    if a is None or b is None:
        return None
    m = mid(ticks[i])
    if m is None:
        return None
    return EntryHit(
        sample=s, entry_idx=i, entry_time=s.event_time, entry_ask=a, entry_bid=b,
        signal_time=s.event_time, signal_score=score, signal_spread_bps=spr,
        entry_spread_bps=spr, confirm_wait_sec=0.0, entry_arm="E1", signal_mid=m,
    )


def resolve_e2(s: Sample, score: float, ticks: Sequence[Tick]) -> Optional[EntryHit]:
    if score < FIXED_THRESHOLD:
        return None
    spr0 = _spr(s)
    if spr0 > 10.0 + 1e-9:
        return None
    i = s.idx
    t0 = s.event_time
    sess = continuous_session_id(t0)
    if sess is None:
        return None
    prior_hi = max_mid_before(ticks, i, 5.0)
    m0 = mid(ticks[i])
    if prior_hi is None or m0 is None:
        return None
    tick = tick_size(m0)
    expire = t0 + timedelta(seconds=CONFIRM_SEC)
    for j in range(i, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            return None
        if t.ts > expire:
            return None
        m = mid(t)
        a = ask(t)
        spr = spread_bps(t)
        if m is None or a is None or spr is None:
            continue
        if spr > spr0 + 1e-9:
            continue
        if m > prior_hi + tick - 1e-12:
            b = bid(t)
            if b is None:
                continue
            return EntryHit(
                sample=s, entry_idx=j, entry_time=t.ts, entry_ask=a, entry_bid=b,
                signal_time=t0, signal_score=score, signal_spread_bps=spr0,
                entry_spread_bps=spr, confirm_wait_sec=(t.ts - t0).total_seconds(),
                entry_arm="E2", signal_mid=m0,
            )
    return None


def resolve_e3(s: Sample, score: float, ticks: Sequence[Tick]) -> Optional[EntryHit]:
    if score < FIXED_THRESHOLD:
        return None
    spr0 = _spr(s)
    if spr0 > 10.0 + 1e-9:
        return None
    i = s.idx
    t0 = s.event_time
    sess = continuous_session_id(t0)
    if sess is None:
        return None
    m0 = mid(ticks[i])
    if m0 is None:
        return None
    expire = t0 + timedelta(seconds=CONFIRM_SEC)
    for j in range(i, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            return None
        if t.ts > expire:
            return None
        m = mid(t)
        a = ask(t)
        spr = spread_bps(t)
        if m is None or a is None or spr is None:
            continue
        if spr > spr0 + 1e-9:
            continue
        if m < m0 - 1e-12:
            continue
        fs = flow_stats(ticks, j, 5.0, t_ref=t.ts)
        br = fs.get("buy_ratio")
        if br is None or br < 0.55 - 1e-12:
            continue
        if fs["buy_q"] <= fs["sell_q"]:
            continue
        b = bid(t)
        if b is None:
            continue
        return EntryHit(
            sample=s, entry_idx=j, entry_time=t.ts, entry_ask=a, entry_bid=b,
            signal_time=t0, signal_score=score, signal_spread_bps=spr0,
            entry_spread_bps=spr, confirm_wait_sec=(t.ts - t0).total_seconds(),
            entry_arm="E3", signal_mid=m0,
        )
    return None


def resolve_e4(
    s: Sample,
    score: float,
    ticks: Sequence[Tick],
    stream_samples: Sequence[tuple[Sample, float]],
    stream_pos: int,
) -> Optional[EntryHit]:
    """Persistence after directional score.

    Prefer 2+ consecutive above-threshold samples spanning ≥1s within 5s.
    Under S1 embargo (~60s) that is usually impossible, so fall back to
    quote-state persistence: score trigger, then mid/bid/spread remain
    non-broken for ≥1s within 5s, enter at that ask.
    """
    if score < FIXED_THRESHOLD:
        return None
    spr0 = _spr(s)
    if spr0 > 10.0 + 1e-9:
        return None
    t0 = s.event_time
    sess = continuous_session_id(t0)
    if sess is None:
        return None
    m0 = mid(ticks[s.idx]) if s.idx < len(ticks) else None
    b0 = bid(ticks[s.idx]) if s.idx < len(ticks) else None
    if m0 is None or b0 is None:
        return None
    tick = tick_size(m0)
    expire = t0 + timedelta(seconds=CONFIRM_SEC)

    # Path A: consecutive scored samples
    for k in range(stream_pos + 1, len(stream_samples)):
        s2, sc2 = stream_samples[k]
        if continuous_session_id(s2.event_time) != sess:
            break
        if s2.event_time > expire:
            break
        if sc2 < FIXED_THRESHOLD:
            break
        dt = (s2.event_time - t0).total_seconds()
        if dt < 1.0 - 1e-9:
            continue
        j = s2.idx
        if j >= len(ticks):
            break
        t = ticks[j]
        m, a, b, spr = mid(t), ask(t), bid(t), spread_bps(t)
        if m is None or a is None or b is None or spr is None:
            break
        if spr > 10.0 + 1e-9 or m < m0 - 1e-12 or b < b0 - tick + 1e-12:
            break
        return EntryHit(
            sample=s, entry_idx=j, entry_time=t.ts, entry_ask=a, entry_bid=b,
            signal_time=t0, signal_score=score, signal_spread_bps=spr0,
            entry_spread_bps=spr, confirm_wait_sec=dt, entry_arm="E4", signal_mid=m0,
        )

    # Path B: quote persistence ≥1s (tick path)
    persist_ok_since = t0
    for j in range(s.idx, len(ticks)):
        t = ticks[j]
        if continuous_session_id(t.ts) != sess:
            return None
        if t.ts > expire:
            return None
        m, a, b, spr = mid(t), ask(t), bid(t), spread_bps(t)
        if m is None or a is None or b is None or spr is None:
            persist_ok_since = t.ts  # reset
            continue
        broken = spr > 10.0 + 1e-9 or m < m0 - 1e-12 or b < b0 - tick + 1e-12
        if broken:
            persist_ok_since = t.ts
            continue
        if (t.ts - persist_ok_since).total_seconds() >= 1.0 - 1e-9 and (t.ts - t0).total_seconds() >= 1.0 - 1e-9:
            return EntryHit(
                sample=s, entry_idx=j, entry_time=t.ts, entry_ask=a, entry_bid=b,
                signal_time=t0, signal_score=score, signal_spread_bps=spr0,
                entry_spread_bps=spr, confirm_wait_sec=(t.ts - t0).total_seconds(),
                entry_arm="E4", signal_mid=m0,
            )
    return None


def resolve_entry(
    arm: str,
    s: Sample,
    score: float,
    ticks: Sequence[Tick],
    stream_samples: Sequence[tuple[Sample, float]] | None = None,
    stream_pos: int = 0,
) -> Optional[EntryHit]:
    if arm == "E1":
        return resolve_e1(s, score, ticks)
    if arm == "E2":
        return resolve_e2(s, score, ticks)
    if arm == "E3":
        return resolve_e3(s, score, ticks)
    if arm == "E4":
        return resolve_e4(s, score, ticks, stream_samples or [], stream_pos)
    return None

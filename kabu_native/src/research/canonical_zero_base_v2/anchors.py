"""Zero-base market-structure anchors (A–O) — multi-event confirmation, not single PUSH."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.loader import Tick


SETUP_TYPES = (
    "impulse_start",
    "pullback_start",
    "pullback_low",
    "reclaim",
    "range_form",
    "breakout_attempt",
    "breakout_hold",
    "ask_wall_form",
    "ask_wall_absorption",
    "ask_wall_break",
    "compression_start",
    "compression_end",
    "expansion_start",
    "failed_setup",
    "price_reset",
)


@dataclass
class Anchor:
    anchor_id: str
    symbol: str
    day: str
    event_id: str
    setup_type: str
    setup_start_time: datetime
    anchor_time: datetime
    exchange_time: str
    received_at: str
    price: float
    canonical_bid: float
    canonical_ask: float
    spread: Optional[float]
    episode_provisional_id: str
    tick_idx: int
    stream_key: str
    evidence: dict[str, Any] = field(default_factory=dict)
    causal_fields: list[str] = field(default_factory=list)
    invalid_reason: str = ""
    strategy_affinity: tuple[str, ...] = ()


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _window_idxs(ticks: Sequence[Tick], i: int, sec: float) -> list[int]:
    t1 = ticks[i].ts
    out = []
    for j in range(i, -1, -1):
        if (t1 - ticks[j].ts).total_seconds() > sec:
            break
        out.append(j)
    return list(reversed(out))


def build_anchors_for_stream(stream_key: str, ticks: Sequence[Tick]) -> list[Anchor]:
    """Generate A–O anchors with multi-tick confirmation (not single-PUSH)."""
    if len(ticks) < 20:
        return []
    out: list[Anchor] = []
    day, symbol = stream_key.split("|", 1)
    ep_n = 0
    last_types: dict[str, int] = {}

    for i in range(15, len(ticks) - 5):
        t = ticks[i]
        if not t.board.canonical_quote_valid:
            continue
        ask = t.board.canonical_best_ask
        bid = t.board.canonical_best_bid
        if ask is None or bid is None or ask <= 0:
            continue
        w30 = _window_idxs(ticks, i, 30)
        w60 = _window_idxs(ticks, i, 60)
        if len(w30) < 5:
            continue
        px = [ticks[j].px for j in w30]
        px60 = [ticks[j].px for j in w60] if len(w60) >= 5 else px
        hi30, lo30 = max(px), min(px)
        ret30 = (px[-1] - px[0]) / px[0] if px[0] > 0 else 0.0
        ret10 = (px[-1] - px[max(0, len(px) - 4)]) / px[max(0, len(px) - 4)] if px[max(0, len(px) - 4)] > 0 else 0.0
        rng30 = (hi30 - lo30) / lo30 if lo30 > 0 else 0.0
        # confirm with next 2 ticks still valid
        if i + 2 >= len(ticks):
            continue
        conf_ok = all(ticks[i + k].board.canonical_quote_valid for k in (1, 2))

        def emit(stype: str, evidence: dict, affinity: tuple[str, ...]) -> None:
            nonlocal ep_n
            # cooldown per type
            if last_types.get(stype, -999) >= i - 8:
                return
            if not conf_ok:
                return
            ep_n += 1
            last_types[stype] = i
            pid = f"{day}:{symbol}:Z0:ep{ep_n}"  # provisional; strategy builders rewrite
            out.append(
                Anchor(
                    anchor_id=f"{day}:{symbol}:{stype}:{i}",
                    symbol=symbol,
                    day=day,
                    event_id=t.event_id,
                    setup_type=stype,
                    setup_start_time=ticks[w30[0]].ts,
                    anchor_time=t.ts,
                    exchange_time=t.exchange_time,
                    received_at=t.received_at,
                    price=t.px,
                    canonical_bid=float(bid),
                    canonical_ask=float(ask),
                    spread=t.board.canonical_spread,
                    episode_provisional_id=pid,
                    tick_idx=i,
                    stream_key=stream_key,
                    evidence=evidence,
                    causal_fields=["px", "Buy1", "Sell1", "TradingVolume", "received_at"],
                    strategy_affinity=affinity,
                )
            )

        # A impulse start
        if ret30 > 0.003 and ret10 > 0.0005:
            emit("impulse_start", {"ret30": ret30, "ret10": ret10}, ("Z1", "Z2"))
        # B/C pullback
        if ret30 > 0.002 and (hi30 - t.px) / hi30 > 0.0015:
            emit("pullback_start", {"dd": (hi30 - t.px) / hi30}, ("Z1",))
        if lo30 == t.px and (hi30 - lo30) / hi30 > 0.001 and ret30 > 0:
            # provisional low: next 2 ticks bounce
            if ticks[i + 1].px >= t.px and ticks[i + 2].px > t.px:
                emit("pullback_low", {"low": t.px, "bounce": True}, ("Z1",))
        # D reclaim
        mid_lvl = (hi30 + lo30) / 2
        if t.px > mid_lvl and px[-3] <= mid_lvl and ret10 > 0:
            emit("reclaim", {"level": mid_lvl}, ("Z1",))
        # E range
        if rng30 < 0.004 and len(w60) >= 10:
            emit("range_form", {"rng30": rng30}, ("Z2", "Z4"))
        # F/G breakout
        if t.px >= hi30 * 0.999 and ret10 > 0.0003 and hi30 > min(px60):
            emit("breakout_attempt", {"high": hi30}, ("Z2",))
            if ticks[i + 1].px >= hi30 * 0.998 and ticks[i + 2].px >= hi30 * 0.998:
                emit("breakout_hold", {"high": hi30, "hold": 2}, ("Z2",))
        # H/I/J ask wall
        aq = t.board.canonical_ask_qty or 0
        bq = t.board.canonical_bid_qty or 0
        prev_aq = ticks[i - 3].board.canonical_ask_qty or aq
        if aq >= max(bq * 1.5, 500):
            emit("ask_wall_form", {"ask_qty": aq}, ("Z3",))
            if aq < prev_aq * 0.85 and (t.px >= ticks[i - 5].px):
                emit("ask_wall_absorption", {"deplete": (prev_aq - aq) / prev_aq}, ("Z3",))
            if ticks[i + 1].px > float(ask) * 0.999 and aq < prev_aq * 0.7:
                emit("ask_wall_break", {"broke": True}, ("Z3",))
        # K/L/M compression / expansion
        rng60 = (max(px60) - min(px60)) / min(px60) if min(px60) > 0 else 1
        if rng30 < 0.0025 and rng60 < 0.005:
            emit("compression_start", {"rng30": rng30}, ("Z4",))
        if rng30 < 0.002 and i > 20:
            prev_rng = (max(px[: max(1, len(px) // 2)]) - min(px[: max(1, len(px) // 2)])) / max(min(px), 1e-9)
            if prev_rng > rng30 * 1.4:
                emit("compression_end", {"prev_rng": prev_rng}, ("Z4",))
        if rng30 > 0.004 and ret10 > 0.0005 and rng60 < 0.008:
            emit("expansion_start", {"rng30": rng30, "ret10": ret10}, ("Z4",))
        # N failed / O reset
        if ret30 > 0.002 and ret10 < -0.001:
            emit("failed_setup", {"ret30": ret30, "ret10": ret10}, ("Z1", "Z2", "Z3", "Z4"))
        if abs(t.px - ticks[i - 10].px) / ticks[i - 10].px > 0.015:
            emit("price_reset", {"move": abs(t.px - ticks[i - 10].px) / ticks[i - 10].px}, ("Z1", "Z2", "Z3", "Z4"))

    return out


def build_all_anchors(streams: dict[str, list[Tick]]) -> list[Anchor]:
    rows: list[Anchor] = []
    for k, ticks in streams.items():
        rows.extend(build_anchors_for_stream(k, ticks))
    rows.sort(key=lambda a: (a.day, a.symbol, a.anchor_time, a.setup_type))
    return rows


def anchor_inventory(anchors: Sequence[Anchor]) -> dict[str, Any]:
    by = {}
    for a in anchors:
        by[a.setup_type] = by.get(a.setup_type, 0) + 1
    return {"total": len(anchors), "by_type": by, "setup_types": list(SETUP_TYPES)}

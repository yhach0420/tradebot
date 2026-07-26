"""Short-lived VCIE episodes — no stale candidate reuse."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_vcie_exact_method.constants import (
    BURST_TO_ENTRY_MAX,
    BURST_TO_SIDE_MAX,
    CROSS_TO_HOLD_MAX,
    SIDE_TO_CROSS_MAX,
)
from research.canonical_vcie_exact_method.features import context_at, liquidity_at, trade_side_at, volume_burst_at
from research.canonical_vcie_exact_method.loader import Tick, exec_ok


@dataclass
class Episode:
    episode_id: str
    day: str
    symbol: str
    stream_key: str
    start_idx: int
    end_idx: int
    start_time: datetime
    end_time: datetime
    status: str  # ENTRY_READY|EXPIRED|FAILED
    fail_reason: str = ""
    breakout_level: Optional[float] = None
    burst_idx: Optional[int] = None
    side_idx: Optional[int] = None
    cross_idx: Optional[int] = None
    entry_idx: Optional[int] = None
    context_type: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    # flags for arms
    has_volume_before_cross: bool = False
    has_side_before_cross: bool = False
    has_hold: bool = False
    liquidity_ok: bool = False


def _session_end(a: Tick, b: Tick) -> bool:
    return a.session != b.session


def _gap(a: Tick, b: Tick) -> bool:
    return (b.ts - a.ts).total_seconds() > 120


def build_episodes(
    stream_key: str,
    ticks: Sequence[Tick],
    *,
    vol_ratio: float = 1.5,
    buy_ratio: float = 0.60,
    hold_mode: str = "events",
    hold_n: float = 2.0,
    expiry_sec: float = 60.0,
    spread_max_bps: Optional[float] = None,
) -> list[Episode]:
    """Detect VCIE episodes with fixed causal order and short expiry."""
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    ep_n = 0
    i = 20
    while i < len(ticks) - 5:
        ctx = context_at(ticks, i)
        if ctx.get("context_type") not in ("HOLD", "CONTROLLED_PULLBACK"):
            i += 1
            continue
        vb = volume_burst_at(ticks, i)
        r10 = vb.get("volume_10s_ratio")
        burst_here = r10 is not None and r10 >= vol_ratio
        # episode can start on ready context; burst may start at i or soon after
        start = i
        level = float(ctx["predefined_breakout_level"])
        burst_idx = i if burst_here else None
        side_idx = None
        cross_idx = None
        entry_idx = None
        status = "EXPIRED"
        fail = ""
        has_vol = False
        has_side = False
        has_hold = False
        liq_ok = False
        feats: dict[str, Any] = {"context": ctx, "volume_at_start": vb}
        j = i
        burst_t0 = ticks[i].ts if burst_here else None

        while j < len(ticks) - 2:
            if j > start and (_gap(ticks[j - 1], ticks[j]) or _session_end(ticks[start], ticks[j])):
                status = "FAILED"
                fail = "session_or_gap"
                break
            # refresh proxy: large gap already handled
            age_from_start = (ticks[j].ts - ticks[start].ts).total_seconds()
            if age_from_start > max(expiry_sec, BURST_TO_ENTRY_MAX) and burst_idx is None and cross_idx is None:
                status = "EXPIRED"
                fail = "context_wait_expiry"
                break

            # detect burst if not yet
            if burst_idx is None:
                vbj = volume_burst_at(ticks, j)
                if vbj.get("volume_10s_ratio") is not None and vbj["volume_10s_ratio"] >= vol_ratio:
                    # context must still be hold/pullback (not already rising)
                    ctxj = context_at(ticks, j)
                    if ctxj.get("context_type") in ("HOLD", "CONTROLLED_PULLBACK"):
                        burst_idx = j
                        burst_t0 = ticks[j].ts
                        level = float(ctxj["predefined_breakout_level"])  # still pre-cross
                        feats["volume_burst"] = vbj
                        feats["context_at_burst"] = ctxj
                    elif ctxj.get("context_type") == "ALREADY_RISING":
                        status = "FAILED"
                        fail = "already_rising"
                        break
            else:
                # expiry from burst
                assert burst_t0 is not None
                if (ticks[j].ts - burst_t0).total_seconds() > min(expiry_sec, BURST_TO_ENTRY_MAX):
                    status = "EXPIRED"
                    fail = "burst_to_entry_expiry"
                    break

            # trade side after burst, before cross
            if burst_idx is not None and side_idx is None and cross_idx is None:
                assert burst_t0 is not None
                if (ticks[j].ts - burst_t0).total_seconds() > BURST_TO_SIDE_MAX:
                    # allow continue to cross for V1/V2 diagnostics without side
                    pass
                else:
                    ts = trade_side_at(ticks, j, sec=10.0)
                    if (
                        ts.get("aggressive_buy_ratio_10s") is not None
                        and ts["aggressive_buy_ratio_10s"] >= buy_ratio
                        and ts.get("trade_direction_confidence", 0) >= 0.55
                    ):
                        side_idx = j
                        feats["trade_side"] = ts

            # price cross of predefined level (level frozen)
            if burst_idx is not None and cross_idx is None:
                px = ticks[j].px
                if px is not None and px > level:
                    # order checks for flags
                    if burst_idx is not None and burst_idx <= j:
                        has_vol = True
                    if side_idx is not None and side_idx <= j:
                        has_side = True
                    # V4 requires side before cross for FULL; still record cross
                    if side_idx is not None and (ticks[j].ts - ticks[side_idx].ts).total_seconds() > SIDE_TO_CROSS_MAX:
                        status = "EXPIRED"
                        fail = "side_to_cross_expiry"
                        break
                    cross_idx = j
                    feats["cross"] = {"level": level, "px": px, "idx": j}
            elif burst_idx is None:
                # V1 diagnostic: allow cross without burst from ready context (price-only arm)
                px = ticks[j].px
                if px is not None and px > level and (ticks[j].ts - ticks[start].ts).total_seconds() <= expiry_sec:
                    cross_idx = j
                    feats["cross"] = {"level": level, "px": px, "idx": j, "no_burst": True}

            # hold after cross
            if cross_idx is not None:
                c0 = ticks[cross_idx].ts
                if (ticks[j].ts - c0).total_seconds() > CROSS_TO_HOLD_MAX:
                    # finalize without hold
                    if entry_idx is None:
                        status = "FAILED"
                        fail = "hold_timeout"
                    break
                # count hold
                hold_ok = False
                if hold_mode == "events":
                    above = 0
                    for k in range(cross_idx, j + 1):
                        p = ticks[k].px
                        if p is not None and p > level:
                            above += 1
                        else:
                            above = 0
                    hold_ok = above >= int(hold_n)
                else:
                    # seconds continuously above
                    if ticks[j].px is not None and ticks[j].px > level:
                        hold_ok = (ticks[j].ts - c0).total_seconds() >= float(hold_n)
                # not returned inside
                returned = ticks[j].px is not None and ticks[j].px <= level
                if hold_ok and not returned:
                    has_hold = True
                    # liquidity
                    liq = liquidity_at(ticks[j])
                    spread_ok = True
                    if spread_max_bps is not None and liq.get("spread_bps") is not None:
                        spread_ok = float(liq["spread_bps"]) <= spread_max_bps
                    liq_ok = bool(liq.get("quote_quality") and exec_ok(ticks[j]) and spread_ok)
                    if exec_ok(ticks[j]):
                        entry_idx = j
                        status = "ENTRY_READY"
                        feats["liquidity"] = liq
                        break
                    else:
                        status = "FAILED"
                        fail = "no_executable_ask"
                        break
                if returned and j > cross_idx:
                    status = "FAILED"
                    fail = "return_inside"
                    break

            j += 1
        else:
            if status not in ("ENTRY_READY", "FAILED"):
                status = "EXPIRED"
                fail = fail or "end_of_stream"

        end = min(j, len(ticks) - 1)
        # For arms that don't need hold: mark entry at cross if executable
        if cross_idx is not None and entry_idx is None and status != "ENTRY_READY":
            # keep cross for V1-V3 evaluation via entry at cross+first valid ask
            if exec_ok(ticks[cross_idx]):
                entry_idx = cross_idx
            else:
                for k in range(cross_idx, min(len(ticks), cross_idx + 10)):
                    if exec_ok(ticks[k]):
                        entry_idx = k
                        break

        ep_n += 1
        out.append(
            Episode(
                episode_id=f"{day}:{symbol}:VCIE:ep{ep_n}",
                day=day,
                symbol=symbol,
                stream_key=stream_key,
                start_idx=start,
                end_idx=end,
                start_time=ticks[start].ts,
                end_time=ticks[end].ts,
                status=status if entry_idx is not None or status == "ENTRY_READY" else status,
                fail_reason=fail,
                breakout_level=level,
                burst_idx=burst_idx,
                side_idx=side_idx,
                cross_idx=cross_idx,
                entry_idx=entry_idx,
                context_type=str(ctx.get("context_type")),
                features=feats,
                has_volume_before_cross=bool(burst_idx is not None and cross_idx is not None and burst_idx <= cross_idx),
                has_side_before_cross=bool(side_idx is not None and cross_idx is not None and side_idx <= cross_idx),
                has_hold=has_hold,
                liquidity_ok=liq_ok,
            )
        )
        i = end + 1
    return out

"""EXIT hysteresis state machine for EC2 noise study."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.eec_noise_hysteresis.constants import AM_FORCE_CLOSE_HM, HARD_STOP_PCT, PM_FORCE_CLOSE_HM
from research.eec_noise_hysteresis.noise import compute_noise_band
from research.pbv2_zero_base_revalidation.util import pnl_5bps
from research.price_flow_exit.path_mfe import PathBar


@dataclass
class HystExit:
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl_5bps: float
    hold_sec: float
    state_path: list[str]
    warning_to_recovery: int
    warning_to_invalidation: int
    invalidation_to_exit_sec: Optional[float]
    false_invalidation: bool


def _session_close(t: datetime) -> datetime:
    h, m = AM_FORCE_CLOSE_HM if t.hour < 12 else PM_FORCE_CLOSE_HM
    return t.replace(hour=h, minute=m, second=0, microsecond=0)


def _exit_px(b: PathBar) -> float:
    if b.bid is not None and b.bid > 0:
        return float(b.bid)
    return float(b.px)


def _vol_weak(path: Sequence[PathBar], i: int) -> Optional[bool]:
    t1 = path[i].t
    recent = prior = 0.0
    for j in range(i, -1, -1):
        dt = (t1 - path[j].t).total_seconds()
        if dt > 40:
            break
        if path[j].volume_delta is None:
            return None
        vd = float(path[j].volume_delta)
        if dt <= 15:
            recent += vd
        else:
            prior += vd
    if prior <= 0:
        return None
    return recent < prior * 0.45


def _sell_tick_up(path: Sequence[PathBar], i: int) -> Optional[bool]:
    t1 = path[i].t
    up = dn = 0.0
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > 15:
            break
        if path[j].volume_delta is None:
            return None
        vd = float(path[j].volume_delta)
        if path[j].tick_direction > 0:
            up += vd
        elif path[j].tick_direction < 0:
            dn += vd
    tot = up + dn
    if tot <= 0:
        return None
    return (dn / tot) >= 0.55


def _spread_worse(path: Sequence[PathBar], i: int) -> Optional[bool]:
    if path[i].spread_bps is None:
        return None
    t1 = path[i].t
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > 30:
            break
        if j == i:
            continue
        if path[j].spread_bps is not None:
            return float(path[i].spread_bps) > float(path[j].spread_bps) + 2.0
    return None


def simulate_hysteresis_exit(
    *,
    entry_time: datetime,
    entry_price: float,
    reclaim: float,
    pullback_low: float,
    path: Sequence[PathBar],
    tick_mult: float,
    range_mult: float,
    spread_mult: float,
    immediate: bool = False,
) -> HystExit:
    """If immediate=True, exit on first reclaim break (A0-like simple)."""
    if not path:
        return HystExit(entry_time, entry_price, "PATH_EMPTY", 0.0, 0.0, ["ACTIVE"], 0, 0, None, False)

    stop = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    close_at = _session_close(entry_time)
    state = "ACTIVE"
    states = ["ACTIVE"]
    warn_rec = warn_inv = 0
    below_since: Optional[float] = None
    below_events = 0
    inv_at: Optional[float] = None
    peak = entry_price
    higher_low = pullback_low

    if immediate:
        for b in path:
            px = _exit_px(b)
            hold = (b.t - entry_time).total_seconds()
            if px <= stop:
                return HystExit(b.t, px, "hard_stop", pnl_5bps(entry_price, px), hold, states + ["EXIT"], 0, 0, None, False)
            if close_at and b.t >= close_at:
                return HystExit(b.t, px, "session_close", pnl_5bps(entry_price, px), hold, states + ["EXIT"], 0, 0, None, False)
            if b.px < reclaim or b.px < pullback_low:
                return HystExit(b.t, px, "immediate_invalidation", pnl_5bps(entry_price, px), hold, states + ["INVALIDATED", "EXIT"], 0, 1, 0.0, False)
        b = path[-1]
        return HystExit(b.t, _exit_px(b), "path_end", pnl_5bps(entry_price, _exit_px(b)), (b.t - entry_time).total_seconds(), states, 0, 0, None, False)

    for i, b in enumerate(path):
        if b.t < entry_time:
            continue
        px = _exit_px(b)
        hold = (b.t - entry_time).total_seconds()
        peak = max(peak, b.px)
        nb = compute_noise_band(path, i, tick_mult=tick_mult, range_mult=range_mult, spread_mult=spread_mult)
        band = float(nb["noise_band"]) if nb["ok"] else None

        if px <= stop:
            return HystExit(b.t, px, "hard_stop", pnl_5bps(entry_price, px), hold, states + ["EXIT"], warn_rec, warn_inv, inv_at, False)
        if close_at and b.t >= close_at:
            return HystExit(b.t, px, "session_close", pnl_5bps(entry_price, px), hold, states + ["EXIT"], warn_rec, warn_inv, inv_at, False)

        if band is None:
            continue

        # RECOVERED
        if state == "WARNING" and b.px > reclaim + band:
            state = "RECOVERED"
            states.append("RECOVERED")
            warn_rec += 1
            below_since = None
            below_events = 0
            state = "ACTIVE"
            states.append("ACTIVE")
            continue
        if state == "WARNING" and b.px >= peak * 0.999 and b.px > reclaim:
            # high update recovery
            state = "RECOVERED"
            states.append("RECOVERED")
            warn_rec += 1
            state = "ACTIVE"
            states.append("ACTIVE")
            below_since = None
            below_events = 0
            continue

        # WARNING triggers
        near_break = b.px <= reclaim + band and b.px >= reclaim - band
        high_stall = b.px < peak and (peak - b.px) <= band
        weak = _vol_weak(path, i)
        if state == "ACTIVE" and (near_break or high_stall or weak is True):
            state = "WARNING"
            states.append("WARNING")

        # INVALIDATED: price + persistence + corroboration
        price_inv = b.px < reclaim - band or b.px < pullback_low
        if price_inv:
            below_events += 1
            below_since = hold if below_since is None else below_since
            dur = hold - below_since
            persist = below_events >= 2 or dur >= 3.0
            corr = False
            corr_ne = 0
            for fn in (_vol_weak, _sell_tick_up, _spread_worse):
                v = fn(path, i)
                if v is True:
                    corr = True
                    break
                if v is None:
                    corr_ne += 1
            # pullback_low breach is structural; if all corroboration NE, still allow invalidate
            if (not corr) and b.px < pullback_low and corr_ne == 3:
                corr = True
            if persist and corr:
                if state == "WARNING":
                    warn_inv += 1
                state = "INVALIDATED"
                states.append("INVALIDATED")
                inv_at = hold
                # exit immediately on INVALIDATED
                # false invalidation: later reclaim+band reclaim within 60s
                false_inv = False
                for b2 in path[i + 1 :]:
                    if (b2.t - b.t).total_seconds() > 60:
                        break
                    if b2.px > reclaim + band:
                        false_inv = True
                        break
                return HystExit(
                    b.t,
                    px,
                    "hysteresis_invalidated",
                    pnl_5bps(entry_price, px),
                    hold,
                    states + ["EXIT"],
                    warn_rec,
                    warn_inv,
                    0.0,
                    false_inv,
                )
        else:
            below_since = None
            below_events = 0

        # trailing-ish: update higher low loosely
        if b.px > higher_low and state == "ACTIVE":
            higher_low = max(higher_low, pullback_low)

    b = path[-1]
    return HystExit(
        b.t,
        _exit_px(b),
        "path_end",
        pnl_5bps(entry_price, _exit_px(b)),
        (b.t - entry_time).total_seconds(),
        states,
        warn_rec,
        warn_inv,
        inv_at,
        False,
    )

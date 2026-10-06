"""Causal swing recognition from CurrentPrice PUSH sequence.

A local high is recognized only AFTER observable rejection:
price approached from below, stalled at a pending extreme, then left downward
by at least rejection_confirm_ticks. Not the max of a fixed lookback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import numpy as np

from research.v2_structural_resistance_detector_validation_v1.rules import DETECTOR_RULES
from research.v2_structural_resistance_detector_validation_v1.tick import jpx_tick_size_yen

Direction = Literal["UP", "DOWN"]


@dataclass
class SwingPoint:
    kind: Literal["HIGH", "LOW"]
    price: float
    price_range: tuple[float, float]
    tick_size: float
    source_t: float
    source_i: int
    recognized_at: float
    recognized_i: int
    incoming_direction: Direction
    outgoing_direction: Direction
    rejection_depth_ticks: float
    rejection_duration_sec: float


@dataclass
class SwingState:
    direction: Optional[Direction] = None
    pending_extreme: Optional[float] = None
    pending_t: Optional[float] = None
    pending_i: Optional[int] = None
    pending_low: Optional[float] = None  # low during up-leg stall
    pending_high: Optional[float] = None  # high during down-leg stall
    last_px: Optional[float] = None
    last_t: Optional[float] = None
    swings: list[SwingPoint] = field(default_factory=list)


def _tick(px: float) -> float:
    return float(jpx_tick_size_yen(float(px)))


def step_swing(
    st: SwingState,
    *,
    i: int,
    t: float,
    px: float,
    rules: dict[str, Any] | None = None,
) -> Optional[SwingPoint]:
    """Advance one CurrentPrice event. Returns a newly recognized swing if any."""
    rules = rules or DETECTOR_RULES
    if not (px == px and px > 0):
        return None
    tick = _tick(px)
    min_leg = float(rules["min_leg_ticks"]) * tick
    confirm = float(rules["rejection_confirm_ticks"]) * tick
    emitted: Optional[SwingPoint] = None

    if st.last_px is None:
        st.last_px = px
        st.last_t = t
        st.pending_extreme = px
        st.pending_t = t
        st.pending_i = i
        st.pending_low = px
        st.pending_high = px
        return None

    if st.direction is None:
        delta = px - float(st.last_px)
        if delta >= min_leg:
            st.direction = "UP"
            st.pending_extreme = px
            st.pending_t = t
            st.pending_i = i
            st.pending_low = px
            st.pending_high = px
        elif delta <= -min_leg:
            st.direction = "DOWN"
            st.pending_extreme = px
            st.pending_t = t
            st.pending_i = i
            st.pending_low = px
            st.pending_high = px
        st.last_px = px
        st.last_t = t
        return None

    if st.direction == "UP":
        assert st.pending_extreme is not None and st.pending_t is not None and st.pending_i is not None
        if px >= float(st.pending_extreme):
            st.pending_extreme = px
            st.pending_t = t
            st.pending_i = i
            st.pending_low = px
            st.pending_high = px
        else:
            if st.pending_low is None or px < float(st.pending_low):
                st.pending_low = px
            depth = float(st.pending_extreme) - px
            if depth >= confirm:
                hi = float(st.pending_extreme)
                lo = float(st.pending_low if st.pending_low is not None else px)
                emitted = SwingPoint(
                    kind="HIGH",
                    price=hi,
                    price_range=(lo, hi),
                    tick_size=_tick(hi),
                    source_t=float(st.pending_t),
                    source_i=int(st.pending_i),
                    recognized_at=t,
                    recognized_i=i,
                    incoming_direction="UP",
                    outgoing_direction="DOWN",
                    rejection_depth_ticks=depth / tick if tick > 0 else 0.0,
                    rejection_duration_sec=t - float(st.pending_t),
                )
                st.swings.append(emitted)
                st.direction = "DOWN"
                st.pending_extreme = px
                st.pending_t = t
                st.pending_i = i
                st.pending_low = px
                st.pending_high = px
    else:  # DOWN
        assert st.pending_extreme is not None and st.pending_t is not None and st.pending_i is not None
        if px <= float(st.pending_extreme):
            st.pending_extreme = px
            st.pending_t = t
            st.pending_i = i
            st.pending_low = px
            st.pending_high = px
        else:
            if st.pending_high is None or px > float(st.pending_high):
                st.pending_high = px
            depth = px - float(st.pending_extreme)
            if depth >= confirm:
                lo = float(st.pending_extreme)
                hi = float(st.pending_high if st.pending_high is not None else px)
                emitted = SwingPoint(
                    kind="LOW",
                    price=lo,
                    price_range=(lo, hi),
                    tick_size=_tick(lo),
                    source_t=float(st.pending_t),
                    source_i=int(st.pending_i),
                    recognized_at=t,
                    recognized_i=i,
                    incoming_direction="DOWN",
                    outgoing_direction="UP",
                    rejection_depth_ticks=depth / tick if tick > 0 else 0.0,
                    rejection_duration_sec=t - float(st.pending_t),
                )
                st.swings.append(emitted)
                st.direction = "UP"
                st.pending_extreme = px
                st.pending_t = t
                st.pending_i = i
                st.pending_low = px
                st.pending_high = px

    st.last_px = px
    st.last_t = t
    return emitted


def run_swings(
    times: np.ndarray,
    prices: np.ndarray,
    *,
    rules: dict[str, Any] | None = None,
) -> list[SwingPoint]:
    st = SwingState()
    for i in range(int(times.size)):
        step_swing(st, i=i, t=float(times[i]), px=float(prices[i]), rules=rules)
    return list(st.swings)

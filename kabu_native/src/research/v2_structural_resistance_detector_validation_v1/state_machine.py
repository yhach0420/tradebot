"""Per-zone state machine. Transitions use only information available at event time."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from research.v2_structural_resistance_detector_validation_v1.rules import DETECTOR_RULES
from research.v2_structural_resistance_detector_validation_v1.zones import StructuralZone

ZoneState = Literal[
    "RESISTANCE_ACTIVE",
    "BREAK_ATTEMPT",
    "BREAK_ACCEPTED_CANDIDATE",
    "RETEST",
    "SUPPORT_CONFIRMED",
    "FAILED_BREAKOUT",
    "INVALIDATED",
]

RetestLabel = Literal["SUPPORT_RESPONSE", "FAILED_SUPPORT", "NO_RETEST", "UNRESOLVED"]


@dataclass
class BreakoutAnatomy:
    first_trade_above_t: Optional[float] = None
    first_trade_above_px: Optional[float] = None
    events_above: int = 0
    max_progress_ticks: float = 0.0
    buy_vol_above: float = 0.0
    sell_vol_above: float = 0.0
    returned_into_zone: bool = False
    returned_below_zone: bool = False
    bid_at_first_above: Optional[float] = None
    ask_at_first_above: Optional[float] = None


@dataclass
class ZoneRuntime:
    zone: StructuralZone
    state: ZoneState = "RESISTANCE_ACTIVE"
    state_t: Optional[float] = None
    anatomy: BreakoutAnatomy = field(default_factory=BreakoutAnatomy)
    retest_label: RetestLabel = "UNRESOLVED"
    retest_t: Optional[float] = None
    support_confirm_t: Optional[float] = None
    failed_t: Optional[float] = None
    history: list[tuple[float, ZoneState]] = field(default_factory=list)

    def set_state(self, t: float, new_state: ZoneState) -> None:
        if new_state != self.state:
            self.state = new_state
            self.state_t = t
            self.history.append((t, new_state))


def is_resistance_like(zone: StructuralZone) -> bool:
    return zone.zone_type in ("UPPER_REJECTION_ZONE", "SINGLE_SWING_HIGH", "UNRESOLVED_ZONE")


def bootstrap_runtime(zone: StructuralZone, *, t: float) -> Optional[ZoneRuntime]:
    """Start a state machine only for non-consolidation zones that look like resistance candidates."""
    if zone.zone_type == "CONSOLIDATION_ZONE":
        return None
    if zone.zone_type == "UNRESOLVED_ZONE" and zone.rejection_n < 1:
        return None
    rt = ZoneRuntime(zone=zone, state="RESISTANCE_ACTIVE", state_t=t)
    rt.history.append((t, "RESISTANCE_ACTIVE"))
    return rt


def step_zone_state(
    rt: ZoneRuntime,
    *,
    t: float,
    px: float,
    bid: float,
    ask: float,
    buy_vol: float,
    sell_vol: float,
    rules: dict[str, Any] | None = None,
) -> None:
    rules = rules or DETECTOR_RULES
    if not (px == px and px > 0):
        return
    if rt.state in ("FAILED_BREAKOUT", "INVALIDATED", "SUPPORT_CONFIRMED"):
        return

    zone = rt.zone
    tick = zone.tick_size if zone.tick_size > 0 else 1.0
    lo, hi = zone.zone_low, zone.zone_high
    min_events = int(rules["break_candidate_min_events_above"])
    min_prog = float(rules["break_candidate_min_progress_ticks"])
    support_away = float(rules["support_response_away_ticks"]) * tick

    above = px > hi
    inside = lo <= px <= hi
    below = px < lo

    if rt.state == "RESISTANCE_ACTIVE":
        if above:
            rt.set_state(t, "BREAK_ATTEMPT")
            rt.anatomy.first_trade_above_t = t
            rt.anatomy.first_trade_above_px = px
            rt.anatomy.events_above = 1
            rt.anatomy.max_progress_ticks = (px - hi) / tick if tick > 0 else 0.0
            rt.anatomy.bid_at_first_above = bid if bid == bid else None
            rt.anatomy.ask_at_first_above = ask if ask == ask else None
            rt.anatomy.buy_vol_above += max(0.0, buy_vol)
            rt.anatomy.sell_vol_above += max(0.0, sell_vol)
        return

    if rt.state == "BREAK_ATTEMPT":
        if above:
            rt.anatomy.events_above += 1
            prog = (px - hi) / tick if tick > 0 else 0.0
            if prog > rt.anatomy.max_progress_ticks:
                rt.anatomy.max_progress_ticks = prog
            rt.anatomy.buy_vol_above += max(0.0, buy_vol)
            rt.anatomy.sell_vol_above += max(0.0, sell_vol)
            if rt.anatomy.events_above >= min_events or rt.anatomy.max_progress_ticks >= min_prog:
                rt.set_state(t, "BREAK_ACCEPTED_CANDIDATE")
        elif inside:
            rt.anatomy.returned_into_zone = True
        elif below:
            rt.anatomy.returned_below_zone = True
            rt.set_state(t, "FAILED_BREAKOUT")
            rt.failed_t = t
            rt.retest_label = "FAILED_SUPPORT"
        return

    if rt.state == "BREAK_ACCEPTED_CANDIDATE":
        if above:
            rt.anatomy.events_above += 1
            prog = (px - hi) / tick if tick > 0 else 0.0
            if prog > rt.anatomy.max_progress_ticks:
                rt.anatomy.max_progress_ticks = prog
            # continuing higher without revisit → NO_RETEST remains provisional
            if rt.retest_label == "UNRESOLVED":
                rt.retest_label = "NO_RETEST"
        elif inside:
            rt.anatomy.returned_into_zone = True
            rt.set_state(t, "RETEST")
            rt.retest_t = t
            rt.retest_label = "UNRESOLVED"
        elif below:
            rt.anatomy.returned_below_zone = True
            rt.set_state(t, "FAILED_BREAKOUT")
            rt.failed_t = t
            rt.retest_label = "FAILED_SUPPORT"
        return

    if rt.state == "RETEST":
        if above and (px - hi) >= support_away:
            rt.set_state(t, "SUPPORT_CONFIRMED")
            rt.support_confirm_t = t
            rt.retest_label = "SUPPORT_RESPONSE"
        elif below:
            rt.set_state(t, "FAILED_BREAKOUT")
            rt.failed_t = t
            rt.retest_label = "FAILED_SUPPORT"
        return

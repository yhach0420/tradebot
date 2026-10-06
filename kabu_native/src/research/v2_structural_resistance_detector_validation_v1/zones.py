"""Cluster rejected swing highs into zones from the observed cluster span.

Zone width is the min/max of member highs (not a fixed ±1 tick sole definition).
Independent tests require material leave-and-return. No composite score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from research.v2_structural_resistance_detector_validation_v1.rules import DETECTOR_RULES
from research.v2_structural_resistance_detector_validation_v1.swings import SwingPoint
from research.v2_structural_resistance_detector_validation_v1.tick import jpx_tick_size_yen

ZoneType = Literal[
    "UPPER_REJECTION_ZONE",
    "CONSOLIDATION_ZONE",
    "SINGLE_SWING_HIGH",
    "UNRESOLVED_ZONE",
]


@dataclass
class RejectionEpisode:
    approach_t: float
    reject_t: float
    high_price: float
    depth_ticks: float
    duration_sec: float
    volume: float


@dataclass
class StructuralZone:
    zone_id: int
    zone_low: float
    zone_high: float
    zone_center: float
    tick_size: float
    first_seen_t: float
    last_test_t: float
    member_highs: list[float] = field(default_factory=list)
    member_source_ts: list[float] = field(default_factory=list)
    independent_test_n: int = 0
    rejection_n: int = 0
    rejections: list[RejectionEpisode] = field(default_factory=list)
    time_inside_sec: float = 0.0
    volume_while_testing: float = 0.0
    through_up_n: int = 0  # enter from below, exit above without downward rejection
    through_down_n: int = 0  # enter from above, exit below without upward rejection
    approach_from_below_n: int = 0
    approach_from_above_n: int = 0
    zone_type: ZoneType = "UNRESOLVED_ZONE"
    # material-away tracking for independent tests
    last_left_below: bool = False
    away_confirmed: bool = True  # first approach is independent
    inside: bool = False
    inside_since: Optional[float] = None
    entered_from: Optional[str] = None  # "below" | "above"
    peak_inside: Optional[float] = None
    last_t: Optional[float] = None
    last_px: Optional[float] = None

    def span_ticks(self) -> float:
        tick = self.tick_size if self.tick_size > 0 else jpx_tick_size_yen(self.zone_center)
        return (self.zone_high - self.zone_low) / tick if tick > 0 else 0.0


def _tick(px: float) -> float:
    return float(jpx_tick_size_yen(float(px)))


def _classify(zone: StructuralZone, rules: dict[str, Any]) -> ZoneType:
    min_tests = int(rules["upper_rejection_min_independent_tests"])
    min_rej = int(rules["upper_rejection_min_rejections"])
    min_through = int(rules["consolidation_min_through_each_way"])
    two_way_approach = zone.approach_from_below_n >= min_through and zone.approach_from_above_n >= min_through
    two_way_through = zone.through_up_n >= min_through and zone.through_down_n >= min_through
    # Two-way traffic without clear upper-rejection asymmetry → consolidation.
    if two_way_through or (
        two_way_approach and zone.approach_from_above_n >= max(1, zone.rejection_n // 2)
    ):
        return "CONSOLIDATION_ZONE"
    if (
        zone.independent_test_n >= min_tests
        and zone.rejection_n >= min_rej
        and zone.approach_from_below_n >= zone.approach_from_above_n
        and zone.rejection_n >= zone.through_up_n
    ):
        return "UPPER_REJECTION_ZONE"
    if len(zone.member_highs) == 1 and zone.rejection_n <= 1 and zone.through_up_n == 0:
        return "SINGLE_SWING_HIGH"
    if len(zone.member_highs) == 1:
        return "SINGLE_SWING_HIGH"
    return "UNRESOLVED_ZONE"


def _expand(zone: StructuralZone, price: float, source_t: float, rules: dict[str, Any]) -> None:
    pad = float(rules["zone_pad_ticks"]) * zone.tick_size
    zone.member_highs.append(price)
    zone.member_source_ts.append(source_t)
    zone.zone_low = min(zone.member_highs) - pad
    zone.zone_high = max(zone.member_highs) + pad
    zone.zone_center = 0.5 * (zone.zone_low + zone.zone_high)
    zone.last_test_t = source_t


def ingest_swing_high(
    zones: list[StructuralZone],
    swing: SwingPoint,
    *,
    next_id: int,
    rules: dict[str, Any] | None = None,
) -> tuple[list[StructuralZone], int, StructuralZone]:
    """Attach a newly recognized swing high to a cluster zone, or create one."""
    rules = rules or DETECTOR_RULES
    tick = swing.tick_size if swing.tick_size > 0 else _tick(swing.price)
    join = float(rules["cluster_join_ticks"]) * tick
    price = float(swing.price)
    chosen: Optional[StructuralZone] = None
    for zone in zones:
        # join if near the observed cluster span
        if zone.zone_low - join <= price <= zone.zone_high + join:
            chosen = zone
            break
    if chosen is None:
        pad = float(rules["zone_pad_ticks"]) * tick
        chosen = StructuralZone(
            zone_id=next_id,
            zone_low=price - pad,
            zone_high=price + pad,
            zone_center=price,
            tick_size=tick,
            first_seen_t=float(swing.recognized_at),
            last_test_t=float(swing.recognized_at),
            member_highs=[price],
            member_source_ts=[float(swing.source_t)],
            independent_test_n=1,
            rejection_n=1,
            rejections=[
                RejectionEpisode(
                    approach_t=float(swing.source_t),
                    reject_t=float(swing.recognized_at),
                    high_price=price,
                    depth_ticks=float(swing.rejection_depth_ticks),
                    duration_sec=float(swing.rejection_duration_sec),
                    volume=0.0,
                )
            ],
            away_confirmed=False,  # need leave before next independent test
        )
        zones.append(chosen)
        next_id += 1
    else:
        _expand(chosen, price, float(swing.recognized_at), rules)
        chosen.rejection_n += 1
        chosen.rejections.append(
            RejectionEpisode(
                approach_t=float(swing.source_t),
                reject_t=float(swing.recognized_at),
                high_price=price,
                depth_ticks=float(swing.rejection_depth_ticks),
                duration_sec=float(swing.rejection_duration_sec),
                volume=0.0,
            )
        )
        if chosen.away_confirmed:
            chosen.independent_test_n += 1
            chosen.away_confirmed = False
        chosen.tick_size = tick
    chosen.zone_type = _classify(chosen, rules)
    return zones, next_id, chosen


def update_zone_path(
    zone: StructuralZone,
    *,
    t: float,
    px: float,
    vol: float,
    rules: dict[str, Any] | None = None,
) -> None:
    """Track approaches, dwell, through-crossings, and material-away for one zone."""
    rules = rules or DETECTOR_RULES
    if not (px == px and px > 0):
        return
    tick = zone.tick_size if zone.tick_size > 0 else _tick(px)
    away = float(rules["material_away_ticks"]) * tick
    lo, hi = zone.zone_low, zone.zone_high

    if zone.last_t is not None and zone.inside:
        zone.time_inside_sec += max(0.0, t - float(zone.last_t))

    was_inside = zone.inside
    now_inside = lo <= px <= hi

    if not was_inside and now_inside:
        zone.inside = True
        zone.inside_since = t
        zone.peak_inside = px
        zone.volume_while_testing += max(0.0, float(vol))
        if zone.last_px is not None:
            if float(zone.last_px) < lo:
                zone.entered_from = "below"
                zone.approach_from_below_n += 1
            elif float(zone.last_px) > hi:
                zone.entered_from = "above"
                zone.approach_from_above_n += 1
            else:
                zone.entered_from = None
    elif was_inside and now_inside:
        zone.volume_while_testing += max(0.0, float(vol))
        if zone.peak_inside is None or px > float(zone.peak_inside):
            zone.peak_inside = px
    elif was_inside and not now_inside:
        # left the zone
        zone.inside = False
        entered = zone.entered_from
        if px < lo:
            if entered == "above":
                zone.through_down_n += 1
            elif entered == "below" and zone.peak_inside is not None and float(zone.peak_inside) >= hi - 1e-12:
                # reached the top of the band then exited down: rejection-like (swing handles count)
                pass
            if (lo - px) >= away:
                zone.away_confirmed = True
                zone.last_left_below = True
        elif px > hi:
            if entered == "below":
                zone.through_up_n += 1
            elif entered == "above":
                # re-entered from above and left upward again
                pass
            zone.last_left_below = False
        zone.entered_from = None
        zone.inside_since = None
        zone.zone_type = _classify(zone, rules)
    # Continuous cross in one step (gap through the band)
    elif (
        not was_inside
        and zone.last_px is not None
        and float(zone.last_px) < lo
        and px > hi
    ):
        zone.through_up_n += 1
        zone.approach_from_below_n += 1
        zone.zone_type = _classify(zone, rules)
    elif (
        not was_inside
        and zone.last_px is not None
        and float(zone.last_px) > hi
        and px < lo
    ):
        zone.through_down_n += 1
        zone.approach_from_above_n += 1
        zone.zone_type = _classify(zone, rules)
    else:
        # still outside
        if px < lo - away:
            zone.away_confirmed = True
            zone.last_left_below = True

    zone.last_t = t
    zone.last_px = px

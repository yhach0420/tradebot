"""Synthetic unit checks for the structural detector. No V2 PnL."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.v2_structural_resistance_detector_validation_v1.detector import SessionDetector


def _path(prices: list[float], dt: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    px = np.asarray(prices, dtype=float)
    t = np.arange(px.size, dtype=float) * dt
    return t, px


def check_upper_rejection() -> bool:
    # Approach 100 twice from below with clear 3+ tick rejections; material leave between.
    # Prices around 90-100 with tick=1 for p<=3000.
    prices = (
        [90, 92, 94, 96, 98, 100, 99, 97, 95, 93, 91]  # first rejection from 100
        + [90, 88, 86, 84]  # material away
        + [86, 90, 94, 97, 99, 100, 98, 96, 94, 92, 90]  # second rejection
    )
    t, px = _path(prices)
    det = SessionDetector()
    det.run_arrays(t, px)
    types = [z.zone_type for z in det.zones]
    return any(z == "UPPER_REJECTION_ZONE" for z in types) or (
        any(z.independent_test_n >= 2 and z.rejection_n >= 2 for z in det.zones)
    )


def check_consolidation() -> bool:
    # Seed a band near 100, then repeatedly cross through both directions to 110 and 90.
    seed = [90, 94, 98, 100, 97, 93, 90]
    crosses = []
    for _ in range(3):
        crosses += [92, 96, 100, 104, 108, 110, 106, 102, 98, 94, 90, 88, 92, 96, 100, 96, 92]
    t, px = _path(seed + crosses)
    det = SessionDetector()
    det.run_arrays(t, px)
    if any(z.zone_type == "CONSOLIDATION_ZONE" for z in det.zones):
        return True
    return any(
        (z.through_up_n >= 2 and z.through_down_n >= 2)
        or (z.approach_from_below_n >= 2 and z.approach_from_above_n >= 2)
        for z in det.zones
    )


def check_single_swing() -> bool:
    prices = [90, 92, 94, 96, 98, 100, 97, 94, 91, 88]
    t, px = _path(prices)
    det = SessionDetector()
    det.run_arrays(t, px)
    return any(z.zone_type == "SINGLE_SWING_HIGH" for z in det.zones) and len(det.swings) >= 1


def check_break_hold_vs_fail() -> bool:
    # Build resistance at 100 with two rejections, then break and hold vs fail.
    base = (
        [90, 94, 98, 100, 97, 93, 88, 86]
        + [90, 95, 99, 100, 96, 92, 88]
    )
    hold = base + [92, 96, 100, 102, 104, 105, 104, 103, 102, 101, 102, 104]
    fail = base + [92, 96, 100, 102, 103, 101, 99, 97, 95, 93]
    th, ph = _path(hold)
    tf, pf = _path(fail)
    dh = SessionDetector()
    dh.run_arrays(th, ph)
    df = SessionDetector()
    df.run_arrays(tf, pf)
    hold_states = {rt.state for rt in dh.runtimes.values()}
    fail_states = {rt.state for rt in df.runtimes.values()}
    hold_ok = bool(hold_states & {"BREAK_ACCEPTED_CANDIDATE", "RETEST", "SUPPORT_CONFIRMED", "BREAK_ATTEMPT"})
    fail_ok = "FAILED_BREAKOUT" in fail_states or any(
        rt.retest_label == "FAILED_SUPPORT" for rt in df.runtimes.values()
    )
    return hold_ok and fail_ok and hold_states != fail_states


def check_next_ignores_future() -> bool:
    # Zone at 110 created late must not be visible as next resistance early.
    early = [90, 94, 98, 100, 96, 92]
    late = early + [93, 95, 100, 105, 108, 110, 107, 104, 100]
    t, px = _path(late)
    det = SessionDetector()
    # run only through early portion
    cut = float(t[len(early) - 1])
    det.run_arrays(t, px, until_t=cut)
    nxt = det.next_resistance(t=cut, px=96.0)
    # After full run, a higher zone may exist
    det2 = SessionDetector()
    det2.run_arrays(t, px)
    late_zones_above = [z for z in det2.zones if z.zone_low > 96]
    # Early next must not reference a zone whose first_seen_t is after cut
    if nxt["status"] == "KNOWN" and nxt.get("zone") is not None:
        if float(nxt["zone"].first_seen_t) > cut + 1e-9:
            return False
    # And future-only highs should not appear in early detector
    for z in det.zones:
        if float(z.first_seen_t) > cut + 1e-9:
            return False
    return True


def self_check() -> dict[str, bool]:
    return {
        "upper_rejection_zone": check_upper_rejection(),
        "consolidation_zone": check_consolidation(),
        "single_swing_high": check_single_swing(),
        "break_hold_vs_fail_distinct": check_break_hold_vs_fail(),
        "next_resistance_ignores_future": check_next_ignores_future(),
    }


def require_self_check() -> dict[str, bool]:
    got = self_check()
    failed = [k for k, v in got.items() if not v]
    if failed:
        raise RuntimeError(f"synthetic_detector_failed:{failed}")
    return got

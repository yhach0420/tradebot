"""Causal structural resistance detector over a PUSH CurrentPrice path.

Does NOT start from PRE_BREAK_HIGH. Scans the path independently, then asks
whether PRE_BREAK_HIGH matched a detected zone.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

import numpy as np

from research.v2_structural_resistance_detector_validation_v1.rules import DETECTOR_RULES
from research.v2_structural_resistance_detector_validation_v1.state_machine import (
    ZoneRuntime,
    bootstrap_runtime,
    is_resistance_like,
    step_zone_state,
)
from research.v2_structural_resistance_detector_validation_v1.swings import SwingPoint, SwingState, step_swing
from research.v2_structural_resistance_detector_validation_v1.zones import (
    StructuralZone,
    ingest_swing_high,
    update_zone_path,
)


def _board_at(board_t: np.ndarray, board_bid: np.ndarray, board_ask: np.ndarray, t: float) -> tuple[float, float]:
    if int(board_t.size) == 0:
        return float("nan"), float("nan")
    j = int(np.searchsorted(board_t, t, side="right")) - 1
    if j < 0:
        return float("nan"), float("nan")
    return float(board_bid[j]), float(board_ask[j])


class SessionDetector:
    """Causal detector for one symbol-session PUSH path."""

    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self.rules = rules or DETECTOR_RULES
        self.swing_st = SwingState()
        self.zones: list[StructuralZone] = []
        self.runtimes: dict[int, ZoneRuntime] = {}
        self.next_zone_id = 1
        self.session_high: Optional[float] = None
        self.session_high_t: Optional[float] = None
        self.swings: list[SwingPoint] = []

    def step(
        self,
        *,
        i: int,
        t: float,
        px: float,
        vol: float = 0.0,
        buy_vol: float = 0.0,
        sell_vol: float = 0.0,
        bid: float = float("nan"),
        ask: float = float("nan"),
    ) -> None:
        if px == px and px > 0:
            if self.session_high is None or px > float(self.session_high):
                self.session_high = px
                self.session_high_t = t

        for zone in self.zones:
            update_zone_path(zone, t=t, px=px, vol=vol, rules=self.rules)
            # refresh classification may enable a runtime
            if zone.zone_id not in self.runtimes and is_resistance_like(zone) and zone.zone_type != "CONSOLIDATION_ZONE":
                if zone.zone_type in ("UPPER_REJECTION_ZONE", "SINGLE_SWING_HIGH") or zone.rejection_n >= 1:
                    rt = bootstrap_runtime(zone, t=t)
                    if rt is not None:
                        self.runtimes[zone.zone_id] = rt

        swing = step_swing(self.swing_st, i=i, t=t, px=px, rules=self.rules)
        if swing is not None and swing.kind == "HIGH":
            self.swings.append(swing)
            self.zones, self.next_zone_id, zone = ingest_swing_high(
                self.zones, swing, next_id=self.next_zone_id, rules=self.rules
            )
            if zone.zone_id not in self.runtimes and zone.zone_type != "CONSOLIDATION_ZONE":
                rt = bootstrap_runtime(zone, t=t)
                if rt is not None:
                    self.runtimes[zone.zone_id] = rt

        for zid, rt in list(self.runtimes.items()):
            # drop consolidations
            if rt.zone.zone_type == "CONSOLIDATION_ZONE":
                rt.set_state(t, "INVALIDATED")
                continue
            step_zone_state(
                rt,
                t=t,
                px=px,
                bid=bid,
                ask=ask,
                buy_vol=buy_vol,
                sell_vol=sell_vol,
                rules=self.rules,
            )

    def run_arrays(
        self,
        times: np.ndarray,
        prices: np.ndarray,
        *,
        vol: np.ndarray | None = None,
        buy: np.ndarray | None = None,
        sell: np.ndarray | None = None,
        board_t: np.ndarray | None = None,
        board_bid: np.ndarray | None = None,
        board_ask: np.ndarray | None = None,
        until_t: float | None = None,
    ) -> None:
        n = int(times.size)
        for i in range(n):
            t = float(times[i])
            if until_t is not None and t > until_t + 1e-12:
                break
            px = float(prices[i])
            v = float(vol[i]) if vol is not None else 0.0
            bv = float(buy[i]) if buy is not None else 0.0
            sv = float(sell[i]) if sell is not None else 0.0
            bid = ask = float("nan")
            if board_t is not None and board_bid is not None and board_ask is not None:
                bid, ask = _board_at(board_t, board_bid, board_ask, t)
            self.step(i=i, t=t, px=px, vol=v, buy_vol=bv, sell_vol=sv, bid=bid, ask=ask)

    def zones_known_at(self, t: float) -> list[StructuralZone]:
        return [z for z in self.zones if float(z.first_seen_t) <= t + 1e-12]

    def next_resistance(self, *, t: float, px: float) -> dict[str, Any]:
        """Nearest already-known structural resistance strictly above px. Causal only."""
        cands: list[StructuralZone] = []
        for zone in self.zones_known_at(t):
            if zone.zone_type == "CONSOLIDATION_ZONE":
                continue
            if not is_resistance_like(zone):
                continue
            rt = self.runtimes.get(zone.zone_id)
            if rt is not None and rt.state in ("FAILED_BREAKOUT", "INVALIDATED", "SUPPORT_CONFIRMED"):
                # broken/converted zones are not overhead resistance
                continue
            if zone.zone_low > px:
                cands.append(zone)
        # session high as structural level if already observed and above px
        session_extra = None
        if self.session_high is not None and self.session_high_t is not None:
            if float(self.session_high_t) <= t + 1e-12 and float(self.session_high) > px:
                # only if not already covered by a zone containing session high
                covered = any(z.zone_low <= float(self.session_high) <= z.zone_high for z in cands)
                if not covered:
                    session_extra = float(self.session_high)
        if not cands and session_extra is None:
            return {"status": "OPEN_ABOVE", "zone": None, "level": None}
        best = None
        best_level = None
        if cands:
            best = min(cands, key=lambda z: z.zone_low)
            best_level = best.zone_low
        if session_extra is not None and (best_level is None or session_extra < best_level):
            return {
                "status": "KNOWN",
                "zone": None,
                "level": session_extra,
                "source": "SESSION_HIGH",
                "zone_type": "SESSION_HIGH",
            }
        assert best is not None
        return {
            "status": "KNOWN",
            "zone": best,
            "level": best.zone_low,
            "source": "STRUCTURAL_ZONE",
            "zone_type": best.zone_type,
            "zone_id": best.zone_id,
            "zone_high": best.zone_high,
            "zone_center": best.zone_center,
        }

    def pre_break_match(self, *, pre_break: float, t: float) -> dict[str, Any]:
        """Ask whether PRE_BREAK_HIGH sits inside a causally known structural zone."""
        if not (pre_break == pre_break):
            return {"matched": False, "zone": None, "reason": "pre_break_nan"}
        for zone in self.zones_known_at(t):
            if zone.zone_type == "CONSOLIDATION_ZONE":
                continue
            if zone.zone_low <= pre_break <= zone.zone_high:
                return {
                    "matched": True,
                    "zone": zone,
                    "zone_id": zone.zone_id,
                    "zone_type": zone.zone_type,
                    "zone_low": zone.zone_low,
                    "zone_high": zone.zone_high,
                    "independent_test_n": zone.independent_test_n,
                    "rejection_n": zone.rejection_n,
                }
        return {"matched": False, "zone": None, "reason": "no_structural_zone_contains_pre_break"}

    def snapshot(self) -> dict[str, Any]:
        return {
            "swing_n": len(self.swings),
            "zone_n": len(self.zones),
            "zones": [zone_to_dict(z) for z in self.zones],
            "runtimes": {str(k): runtime_to_dict(v) for k, v in self.runtimes.items()},
            "session_high": self.session_high,
            "session_high_t": self.session_high_t,
        }


def zone_to_dict(z: StructuralZone) -> dict[str, Any]:
    return {
        "zone_id": z.zone_id,
        "zone_low": z.zone_low,
        "zone_high": z.zone_high,
        "zone_center": z.zone_center,
        "tick_size": z.tick_size,
        "first_seen_t": z.first_seen_t,
        "last_test_t": z.last_test_t,
        "independent_test_n": z.independent_test_n,
        "rejection_n": z.rejection_n,
        "through_up_n": z.through_up_n,
        "through_down_n": z.through_down_n,
        "approach_from_below_n": z.approach_from_below_n,
        "approach_from_above_n": z.approach_from_above_n,
        "time_inside_sec": z.time_inside_sec,
        "volume_while_testing": z.volume_while_testing,
        "zone_type": z.zone_type,
        "member_highs": list(z.member_highs),
        "rejection_depths": [r.depth_ticks for r in z.rejections],
        "rejection_durations": [r.duration_sec for r in z.rejections],
    }


def runtime_to_dict(rt: ZoneRuntime) -> dict[str, Any]:
    a = rt.anatomy
    return {
        "zone_id": rt.zone.zone_id,
        "state": rt.state,
        "state_t": rt.state_t,
        "retest_label": rt.retest_label,
        "retest_t": rt.retest_t,
        "support_confirm_t": rt.support_confirm_t,
        "failed_t": rt.failed_t,
        "history": list(rt.history),
        "anatomy": {
            "first_trade_above_t": a.first_trade_above_t,
            "first_trade_above_px": a.first_trade_above_px,
            "events_above": a.events_above,
            "max_progress_ticks": a.max_progress_ticks,
            "buy_vol_above": a.buy_vol_above,
            "sell_vol_above": a.sell_vol_above,
            "returned_into_zone": a.returned_into_zone,
            "returned_below_zone": a.returned_below_zone,
            "bid_at_first_above": a.bid_at_first_above,
            "ask_at_first_above": a.ask_at_first_above,
        },
    }


def detect_session(
    times: np.ndarray,
    prices: np.ndarray,
    **kwargs: Any,
) -> SessionDetector:
    det = SessionDetector()
    det.run_arrays(times, prices, **kwargs)
    return det

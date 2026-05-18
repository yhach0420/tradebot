"""
Phase 31: State-based persistence engine (Logic Lab).

EXIT/HOLD from sustained bullish/bearish state — not fixed wall-clock thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

# Global persistence thresholds (not per symbol/day/time-of-day)
BULLISH_INSTANT_THRESHOLD = 0.48
BEARISH_INSTANT_THRESHOLD = 0.48
BULLISH_PERSIST_TICKS_HOLD = 4
BEARISH_PERSIST_TICKS_EXIT = 5
STRUCTURE_BREAK_PERSIST_TICKS = 4
RECOVERY_PERSIST_TICKS = 3
MIN_WARMUP_TICKS = 3
BEARISH_EXIT_SCORE = 0.55
STRUCTURE_BREAK_SCORE = 0.62
SPREAD_STABLE_MAX = 1.30
SPREAD_DETERIORATE_MIN = 1.35


@dataclass
class StatePersistenceEngine:
    """Tick-based state persistence (event evaluations, not fixed seconds)."""

    eval_tick_count: int = 0
    bullish_instant: float = 0.0
    bearish_instant: float = 0.0
    bullish_persist_ticks: int = 0
    bearish_persist_ticks: int = 0
    max_bullish_persist_ticks: int = 0
    max_bearish_persist_ticks: int = 0
    structure_break_persist_ticks: int = 0
    recovery_persist_ticks: int = 0
    dominant_state: str = "neutral"
    prev_dominant: str = "neutral"
    bullish_to_bearish_count: int = 0
    transition_paths: list[str] = field(default_factory=list)
    state_hold_events: int = 0
    state_exit_signals: int = 0
    fixed_time_proxy_fired: bool = False

    def _record_transition(self, path: str) -> None:
        self.transition_paths.append(path)
        if len(self.transition_paths) > 32:
            self.transition_paths = self.transition_paths[-32:]
        if path == "bullish_to_bearish":
            self.bullish_to_bearish_count += 1

    def _bullish_components(self, rt: "MicrostructureRuntime", *, mom: float, vwap_dist: Optional[float]) -> dict[str, float]:
        vwap_ok = rt.reclaim_persistent() or (
            vwap_dist is not None and vwap_dist > 0.02 and rt.vwap_reclaim_achieved
        )
        fav_ok = rt.favorable_persistent() or rt.max_favorable_pct >= 0.05
        mom_ok = mom >= 0.0 and rt.momentum_negative_streak < 2
        spread_ok = rt.spread_expansion_ratio < SPREAD_STABLE_MAX
        imb_ok = True
        if rt.entry_imbalance is not None and rt._last_imb is not None:
            imb_ok = float(rt._last_imb) >= float(rt.entry_imbalance) - 0.03
        return {
            "vwap_reclaim_persistence": 1.0 if vwap_ok else 0.0,
            "favorable_persistence": 1.0 if fav_ok else 0.0,
            "momentum_persistence": 1.0 if mom_ok else 0.0,
            "spread_stabilization": 1.0 if spread_ok else 0.0,
            "imbalance_recovery": 1.0 if imb_ok else 0.0,
        }

    def _bearish_components(self, rt: "MicrostructureRuntime", *, mom: float, vwap_dist: Optional[float]) -> dict[str, float]:
        adv_ok = rt.max_adverse_pct <= -0.08 or rt.adverse_persistence_count >= 3
        vwap_fail = rt.below_vwap_seen and not rt.reclaim_persistent()
        if vwap_dist is not None and vwap_dist < -0.04:
            vwap_fail = True
        mom_neg = rt.momentum_negative_streak >= 2 or mom < -0.04
        spread_bad = rt.spread_expansion_ratio >= SPREAD_DETERIORATE_MIN
        imb_col = rt.imbalance_collapse_streak >= 2 or rt.max_imbalance_collapse_streak >= 3
        return {
            "adverse_persistence": 1.0 if adv_ok else 0.0,
            "vwap_failure_persistence": 1.0 if vwap_fail else 0.0,
            "momentum_negative_persistence": 1.0 if mom_neg else 0.0,
            "spread_deterioration_persistence": 1.0 if spread_bad else 0.0,
            "imbalance_collapse_persistence": 1.0 if imb_col else 0.0,
        }

    def update_from_runtime(
        self,
        rt: "MicrostructureRuntime",
        *,
        price: float,
        vwap_dist: Optional[float],
    ) -> None:
        self.eval_tick_count += 1
        mom = rt.current_momentum_pct(price)

        bull_parts = self._bullish_components(rt, mom=mom, vwap_dist=vwap_dist)
        bear_parts = self._bearish_components(rt, mom=mom, vwap_dist=vwap_dist)
        self.bullish_instant = sum(bull_parts.values()) / max(len(bull_parts), 1)
        self.bearish_instant = sum(bear_parts.values()) / max(len(bear_parts), 1)

        if self.bullish_instant >= BULLISH_INSTANT_THRESHOLD:
            self.bullish_persist_ticks += 1
        else:
            self.bullish_persist_ticks = 0
        if self.bearish_instant >= BEARISH_INSTANT_THRESHOLD:
            self.bearish_persist_ticks += 1
        else:
            self.bearish_persist_ticks = 0

        self.max_bullish_persist_ticks = max(self.max_bullish_persist_ticks, self.bullish_persist_ticks)
        self.max_bearish_persist_ticks = max(self.max_bearish_persist_ticks, self.bearish_persist_ticks)

        if rt.recovered_after_adverse and self.bullish_instant >= 0.4:
            self.recovery_persist_ticks += 1
        elif not rt.recovered_after_adverse:
            self.recovery_persist_ticks = 0

        struct_inst = (
            self.bearish_instant >= 0.6
            and self.bullish_instant < 0.35
            and bear_parts["vwap_failure_persistence"] > 0
            and bear_parts["imbalance_collapse_persistence"] > 0
        )
        if struct_inst:
            self.structure_break_persist_ticks += 1
        else:
            self.structure_break_persist_ticks = max(0, self.structure_break_persist_ticks - 1)

        self.prev_dominant = self.dominant_state
        if self.bullish_persist_ticks >= BULLISH_PERSIST_TICKS_HOLD and self.bullish_instant > self.bearish_instant:
            self.dominant_state = "bullish"
        elif self.bearish_persist_ticks >= 3 and self.bearish_instant > self.bullish_instant:
            self.dominant_state = "bearish"
        else:
            self.dominant_state = "neutral"

        if self.prev_dominant == "bullish" and self.dominant_state == "bearish":
            self._record_transition("bullish_to_bearish")
        elif self.prev_dominant == "neutral" and self.dominant_state == "bearish":
            self._record_transition("neutral_to_bearish")
        elif self.prev_dominant == "bearish" and self.dominant_state == "bullish":
            self._record_transition("bearish_to_bullish")

        elapsed = self.eval_tick_count
        if elapsed == 12 and self.bearish_instant >= BEARISH_EXIT_SCORE:
            self.fixed_time_proxy_fired = True

    def should_hold(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.bullish_persist_ticks >= BULLISH_PERSIST_TICKS_HOLD:
            return True
        if self.recovery_persist_ticks >= RECOVERY_PERSIST_TICKS:
            return True
        if self.dominant_state == "bullish" and self.bullish_instant >= BULLISH_INSTANT_THRESHOLD:
            return True
        return False

    def bearish_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.bearish_persist_ticks >= BEARISH_PERSIST_TICKS_EXIT
            and self.bearish_instant >= BEARISH_EXIT_SCORE
            and self.bullish_persist_ticks < 2
        )

    def structure_break_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.structure_break_persist_ticks >= STRUCTURE_BREAK_PERSIST_TICKS
            and self.bearish_instant >= STRUCTURE_BREAK_SCORE
            and self.bullish_instant < 0.35
        )

    def recovery_failed_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.bearish_persist_ticks >= 4
            and self.recovery_persist_ticks > 0
            and self.recovery_persist_ticks < RECOVERY_PERSIST_TICKS
            and self.dominant_state == "bearish"
        )

    def finalize_dict(self) -> dict[str, Any]:
        return {
            "state_eval_ticks": self.eval_tick_count,
            "bullish_instant_score": round(self.bullish_instant, 4),
            "bearish_instant_score": round(self.bearish_instant, 4),
            "bullish_persist_ticks": self.bullish_persist_ticks,
            "bearish_persist_ticks": self.bearish_persist_ticks,
            "max_bullish_persist_ticks": self.max_bullish_persist_ticks,
            "max_bearish_persist_ticks": self.max_bearish_persist_ticks,
            "recovery_persist_ticks": self.recovery_persist_ticks,
            "structure_break_persist_ticks": self.structure_break_persist_ticks,
            "dominant_state_final": self.dominant_state,
            "bullish_to_bearish_transitions": self.bullish_to_bearish_count,
            "state_transition_paths": list(self.transition_paths),
            "state_hold_events": self.state_hold_events,
            "fixed_time_proxy_fired": self.fixed_time_proxy_fired,
            "state_based_exit": self.state_exit_signals > 0,
        }

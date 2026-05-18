"""
Phase 33: Duration-weighted persistence engine (Logic Lab).

EXIT/HOLD from weighted duration × quality — short bearish treated as noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

# Global duration scales (ticks, not wall-clock seconds)
DURATION_SCALE = 12.0
SHORT_BEARISH_NOISE_TICKS = 2
MIN_WARMUP_TICKS = 3
BULLISH_HOLD_WEIGHTED = 0.52
BEARISH_EXIT_WEIGHTED = 0.58
COLLAPSE_EXIT_WEIGHTED = 0.62
DECAY_BULLISH_DROP = 4
BEARISH_WEIGHT_RISE_TICKS = 3
NEUTRAL_STABILIZE_TICKS = 4
STRUCTURE_BEARISH_WEIGHTED = 0.65


@dataclass
class DurationWeightedEngine:
    """Duration × persistence quality scores for v11 EXIT/HOLD."""

    eval_tick_count: int = 0
    bullish_weighted_score: float = 0.0
    bearish_weighted_score: float = 0.0
    prev_bullish_weighted: float = 0.0
    prev_bearish_weighted: float = 0.0

    reclaim_weighted: float = 0.0
    favorable_weighted: float = 0.0
    momentum_weighted: float = 0.0
    adverse_weighted: float = 0.0
    collapse_weighted: float = 0.0
    vwap_fail_weighted: float = 0.0
    neutral_stabilization_weight: float = 0.0

    max_bullish_weighted: float = 0.0
    max_bearish_weighted: float = 0.0
    max_bullish_duration_seen: int = 0
    bearish_weighted_rise_ticks: int = 0

    bullish_decay_detected: bool = False
    collapse_weighted_ready: bool = False
    structure_break_weighted_ready: bool = False
    short_bearish_noise: bool = False

    weighted_hold_events: int = 0
    weighted_exit_signals: int = 0
    weighted_hold_active: bool = False
    fixed_time_proxy_fired: bool = False

    def _norm(self, ticks: float, *, quality: float = 1.0) -> float:
        if ticks <= 0:
            return 0.0
        dur = min(1.0, float(ticks) / DURATION_SCALE)
        return dur * max(0.0, min(1.0, quality))

    def update_from_runtime(self, rt: "MicrostructureRuntime") -> None:
        self.eval_tick_count += 1
        eng = rt.state_engine
        trans = rt.transition_engine

        bull_dur = trans.bullish_duration_ticks
        bear_dur = trans.bearish_duration_ticks
        neut_dur = trans.neutral_duration_ticks
        self.max_bullish_duration_seen = max(self.max_bullish_duration_seen, trans.max_bullish_duration)

        bull_q = eng.bullish_instant
        bear_q = eng.bearish_instant

        self.reclaim_weighted = self._norm(rt.reclaim_persist_ticks, quality=bull_q)
        self.favorable_weighted = self._norm(
            min(rt.favorable_persistence_count, 12),
            quality=0.5 + min(rt.max_favorable_pct, 0.15),
        )
        mom_ticks = bull_dur if bull_q >= 0.45 and rt.momentum_negative_streak < 2 else 0
        self.momentum_weighted = self._norm(mom_ticks, quality=bull_q)

        self.bullish_weighted_score = min(
            1.0,
            0.28 * self._norm(bull_dur, quality=bull_q)
            + 0.24 * self.reclaim_weighted
            + 0.24 * self.favorable_weighted
            + 0.24 * self.momentum_weighted,
        )

        effective_bear = bear_dur if bear_dur > SHORT_BEARISH_NOISE_TICKS else 0
        self.short_bearish_noise = 0 < bear_dur <= SHORT_BEARISH_NOISE_TICKS

        self.adverse_weighted = self._norm(
            min(rt.adverse_persistence_count, 12),
            quality=bear_q if rt.max_adverse_pct <= -0.06 else bear_q * 0.5,
        )
        collapse_dur = trans.collapse_bearish_ticks + (
            trans.max_bearish_duration if trans.collapse_ready else 0
        )
        self.collapse_weighted = self._norm(collapse_dur, quality=trans.collapse_transition_score)

        vwap_fail_dur = trans.bearish_duration_ticks if (
            rt.below_vwap_seen and not rt.reclaim_persistent()
        ) else 0
        self.vwap_fail_weighted = self._norm(vwap_fail_dur, quality=bear_q)

        self.bearish_weighted_score = min(
            1.0,
            0.28 * self._norm(effective_bear, quality=bear_q)
            + 0.24 * self.adverse_weighted
            + 0.28 * self.collapse_weighted
            + 0.20 * self.vwap_fail_weighted,
        )

        self.neutral_stabilization_weight = self._norm(neut_dur, quality=0.7)

        if self.bearish_weighted_score > self.prev_bearish_weighted + 0.04:
            self.bearish_weighted_rise_ticks += 1
        else:
            self.bearish_weighted_rise_ticks = max(0, self.bearish_weighted_rise_ticks - 1)

        if (
            self.max_bullish_duration_seen >= DECAY_BULLISH_DROP + 2
            and bull_dur <= 1
            and self.bullish_weighted_score < self.prev_bullish_weighted - 0.15
        ):
            self.bullish_decay_detected = True

        self.collapse_weighted_ready = (
            self.bearish_weighted_score >= COLLAPSE_EXIT_WEIGHTED
            and self.bearish_weighted_rise_ticks >= BEARISH_WEIGHT_RISE_TICKS
            and self.collapse_weighted >= 0.35
        )

        self.structure_break_weighted_ready = (
            self.bearish_weighted_score >= STRUCTURE_BEARISH_WEIGHTED
            and effective_bear >= SHORT_BEARISH_NOISE_TICKS + 3
            and self.bullish_weighted_score < 0.35
        )

        self.max_bullish_weighted = max(self.max_bullish_weighted, self.bullish_weighted_score)
        self.max_bearish_weighted = max(self.max_bearish_weighted, self.bearish_weighted_score)

        self.weighted_hold_active = self.should_hold()

        self.prev_bullish_weighted = self.bullish_weighted_score
        self.prev_bearish_weighted = self.bearish_weighted_score

        if self.eval_tick_count == 12 and self.bearish_weighted_score >= 0.55:
            self.fixed_time_proxy_fired = True

    def should_hold(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.bullish_weighted_score >= BULLISH_HOLD_WEIGHTED:
            return True
        if self.neutral_stabilization_weight >= 0.35 and self.bullish_weighted_score >= 0.38:
            return True
        if self.short_bearish_noise and self.bullish_weighted_score >= 0.42:
            return True
        if self.reclaim_weighted >= 0.4 and self.favorable_weighted >= 0.3:
            return True
        return False

    def bearish_weighted_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.short_bearish_noise:
            return False
        return (
            self.bearish_weighted_score >= BEARISH_EXIT_WEIGHTED
            and self.bearish_weighted_rise_ticks >= 2
            and self.bullish_weighted_score < BULLISH_HOLD_WEIGHTED - 0.08
        )

    def decay_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.bullish_decay_detected
            and self.bearish_weighted_score >= BEARISH_EXIT_WEIGHTED - 0.08
        )

    def finalize_dict(self) -> dict[str, Any]:
        return {
            "bullish_weighted_score": round(self.bullish_weighted_score, 4),
            "bearish_weighted_score": round(self.bearish_weighted_score, 4),
            "max_bullish_weighted": round(self.max_bullish_weighted, 4),
            "max_bearish_weighted": round(self.max_bearish_weighted, 4),
            "reclaim_weighted": round(self.reclaim_weighted, 4),
            "favorable_weighted": round(self.favorable_weighted, 4),
            "collapse_weighted": round(self.collapse_weighted, 4),
            "neutral_stabilization_weight": round(self.neutral_stabilization_weight, 4),
            "bullish_decay_detected": self.bullish_decay_detected,
            "collapse_weighted_ready": self.collapse_weighted_ready,
            "structure_break_weighted_ready": self.structure_break_weighted_ready,
            "short_bearish_noise": self.short_bearish_noise,
            "max_bullish_duration_ticks": self.max_bullish_duration_seen,
            "weighted_hold_events": self.weighted_hold_events,
            "weighted_hold_active_final": self.weighted_hold_active,
            "fixed_time_proxy_fired": self.fixed_time_proxy_fired,
        }

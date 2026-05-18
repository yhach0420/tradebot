"""
Phase 34: Bullish continuation prioritization engine (Logic Lab).

HOLD while bullish continuation persists; EXIT on continuation loss / decay / bearish accumulation.
Short bearish bursts are noise — not structure breaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

CONTINUATION_SCALE = 14.0
SHORT_BEARISH_NOISE_TICKS = 2
MIN_WARMUP_TICKS = 3
CONTINUATION_HOLD_SCORE = 0.50
CONTINUATION_LOSS_SCORE = 0.32
CONTINUATION_LOSS_TICKS = 3
CONTINUATION_DECAY_DROP = 0.18
BEARISH_ACCUM_EXIT = 0.55
BEARISH_ACCUM_TICKS = 4
STRUCTURE_DETERIORATION = 0.58
SPREAD_STAB_MAX_EXPANSION = 0.50


@dataclass
class BullishContinuationEngine:
    """Bullish continuation score and duration-driven HOLD/EXIT signals."""

    eval_tick_count: int = 0
    bullish_continuation_score: float = 0.0
    continuation_duration_ticks: int = 0
    max_continuation_duration: int = 0
    favorable_continuation: float = 0.0
    momentum_continuation: float = 0.0
    reclaim_continuation: float = 0.0
    spread_stabilization: float = 0.0

    prev_continuation_score: float = 0.0
    max_continuation_score: float = 0.0
    continuation_decay_detected: bool = False
    continuation_recovery_detected: bool = False
    continuation_failure_streak: int = 0

    bearish_accumulation_score: float = 0.0
    bearish_accumulation_ticks: int = 0
    structure_deterioration_score: float = 0.0

    short_bearish_noise: bool = False
    continuation_hold_active: bool = False
    continuation_loss_streak: int = 0

    continuation_hold_events: int = 0
    continuation_exit_signals: int = 0
    fixed_time_proxy_fired: bool = False

    def _norm_duration(self, ticks: int) -> float:
        if ticks <= 0:
            return 0.0
        return min(1.0, float(ticks) / CONTINUATION_SCALE)

    def update_from_runtime(self, rt: "MicrostructureRuntime") -> None:
        self.eval_tick_count += 1
        dw = rt.duration_engine
        trans = rt.transition_engine

        bear_dur = trans.bearish_duration_ticks
        self.short_bearish_noise = 0 < bear_dur <= SHORT_BEARISH_NOISE_TICKS

        if rt.entry_spread_bps and rt.spread_expansion_ratio > 0:
            exp = max(0.0, float(rt.spread_expansion_ratio) - 1.0)
            self.spread_stabilization = max(0.0, 1.0 - min(1.0, exp / SPREAD_STAB_MAX_EXPANSION))
        else:
            self.spread_stabilization = 0.5

        self.favorable_continuation = dw.favorable_weighted
        self.momentum_continuation = dw.momentum_weighted
        self.reclaim_continuation = dw.reclaim_weighted

        self.bullish_continuation_score = min(
            1.0,
            0.30 * dw.bullish_weighted_score
            + 0.22 * self.favorable_continuation
            + 0.22 * self.reclaim_continuation
            + 0.18 * self.momentum_continuation
            + 0.08 * self.spread_stabilization,
        )

        if self.bullish_continuation_score >= CONTINUATION_HOLD_SCORE - 0.06:
            self.continuation_duration_ticks += 1
            self.continuation_failure_streak = 0
        else:
            self.continuation_duration_ticks = max(0, self.continuation_duration_ticks - 1)
            if self.max_continuation_duration >= 5:
                self.continuation_failure_streak += 1

        self.max_continuation_duration = max(
            self.max_continuation_duration, self.continuation_duration_ticks
        )
        self.max_continuation_score = max(
            self.max_continuation_score, self.bullish_continuation_score
        )

        if (
            self.prev_continuation_score < CONTINUATION_LOSS_SCORE
            and self.bullish_continuation_score >= CONTINUATION_HOLD_SCORE
        ):
            self.continuation_recovery_detected = True

        if (
            self.max_continuation_score >= CONTINUATION_HOLD_SCORE + 0.12
            and self.bullish_continuation_score
            < self.max_continuation_score - CONTINUATION_DECAY_DROP
        ):
            fade = rt.favorable_faded() or rt.momentum_negative_streak >= 2
            reclaim_lost = not rt.reclaim_persistent() and rt.below_vwap_seen
            if fade or reclaim_lost or dw.bullish_decay_detected:
                self.continuation_decay_detected = True

        effective_bear = bear_dur if bear_dur > SHORT_BEARISH_NOISE_TICKS else 0
        if effective_bear > 0 and dw.bearish_weighted_score >= 0.35:
            self.bearish_accumulation_ticks += 1
            self.bearish_accumulation_score = min(
                1.0,
                self.bearish_accumulation_score + 0.12 * dw.bearish_weighted_score,
            )
        else:
            self.bearish_accumulation_ticks = max(0, self.bearish_accumulation_ticks - 1)
            self.bearish_accumulation_score = max(
                0.0, self.bearish_accumulation_score - 0.06
            )

        struct = min(
            1.0,
            0.35 * min(1.0, rt.structure_break_score)
            + 0.30 * min(1.0, rt.max_imbalance_collapse_streak / 6.0)
            + 0.35 * dw.adverse_weighted,
        )
        self.structure_deterioration_score = struct

        if self.bullish_continuation_score < CONTINUATION_LOSS_SCORE:
            self.continuation_loss_streak += 1
        else:
            self.continuation_loss_streak = 0

        self.continuation_hold_active = self.should_hold_continuation()
        self.prev_continuation_score = self.bullish_continuation_score

        if self.eval_tick_count == 12 and self.bullish_continuation_score < 0.25:
            self.fixed_time_proxy_fired = True

    def should_hold_continuation(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.bullish_continuation_score >= CONTINUATION_HOLD_SCORE:
            return True
        if self.continuation_duration_ticks >= 4 and self.bullish_continuation_score >= 0.42:
            return True
        if self.short_bearish_noise and self.bullish_continuation_score >= 0.40:
            return True
        if (
            self.reclaim_continuation >= 0.38
            and self.favorable_continuation >= 0.30
        ):
            return True
        neut = self.structure_deterioration_score < 0.35
        if neut and self.bullish_continuation_score >= 0.38:
            return True
        return False

    def continuation_loss_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.short_bearish_noise:
            return False
        return (
            self.max_continuation_duration >= 5
            and self.continuation_loss_streak >= CONTINUATION_LOSS_TICKS
            and self.bullish_continuation_score < CONTINUATION_LOSS_SCORE
        )

    def decay_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return self.continuation_decay_detected and self.bearish_accumulation_score >= 0.25

    def bearish_accumulation_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.short_bearish_noise:
            return False
        return (
            self.bearish_accumulation_score >= BEARISH_ACCUM_EXIT
            and self.bearish_accumulation_ticks >= BEARISH_ACCUM_TICKS
            and self.bullish_continuation_score < CONTINUATION_HOLD_SCORE - 0.05
        )

    def structure_deterioration_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.structure_deterioration_score >= STRUCTURE_DETERIORATION
            and self.bullish_continuation_score < CONTINUATION_HOLD_SCORE - 0.10
            and not self.should_hold_continuation()
        )

    def finalize_dict(self) -> dict[str, Any]:
        return {
            "bullish_continuation_score": round(self.bullish_continuation_score, 4),
            "continuation_duration_ticks": self.continuation_duration_ticks,
            "max_continuation_duration": self.max_continuation_duration,
            "favorable_continuation": round(self.favorable_continuation, 4),
            "momentum_continuation": round(self.momentum_continuation, 4),
            "reclaim_continuation": round(self.reclaim_continuation, 4),
            "spread_stabilization": round(self.spread_stabilization, 4),
            "continuation_decay_detected": self.continuation_decay_detected,
            "continuation_recovery_detected": self.continuation_recovery_detected,
            "continuation_failure_streak": self.continuation_failure_streak,
            "bearish_accumulation_score": round(self.bearish_accumulation_score, 4),
            "bearish_accumulation_ticks": self.bearish_accumulation_ticks,
            "structure_deterioration_score": round(self.structure_deterioration_score, 4),
            "short_bearish_noise": self.short_bearish_noise,
            "continuation_hold_active_final": self.continuation_hold_active,
            "continuation_hold_events": self.continuation_hold_events,
            "fixed_time_proxy_fired": self.fixed_time_proxy_fired,
        }

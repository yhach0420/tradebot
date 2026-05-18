"""
Phase 35: Momentum continuation priority engine (Logic Lab).

HOLD while momentum continuation persists; EXIT on momentum loss / decay / weakness / bearish accumulation.
Short bearish and brief favorable fades are noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research.microstructure_runtime import MicrostructureRuntime

MOMENTUM_DURATION_SCALE = 14.0
SHORT_BEARISH_NOISE_TICKS = 2
SHORT_FAVORABLE_FADE_TICKS = 2
MIN_WARMUP_TICKS = 3
MOMENTUM_HOLD_SCORE = 0.48
MOMENTUM_LOSS_SCORE = 0.30
MOMENTUM_LOSS_TICKS = 3
MOMENTUM_DECAY_DROP = 0.16
BEARISH_ACCUM_EXIT = 0.54
BEARISH_ACCUM_TICKS = 4
WEAKNESS_ACCUM_TICKS = 4
WEAKNESS_SCORE = 0.38


@dataclass
class ContinuationMomentumEngine:
    """Momentum-first continuation score and persistence-driven HOLD/EXIT."""

    eval_tick_count: int = 0
    momentum_continuation_score: float = 0.0
    momentum_continuation_duration: int = 0
    max_momentum_continuation_duration: int = 0
    favorable_continuation: float = 0.0
    positive_momentum_persistence: float = 0.0
    bullish_duration_component: float = 0.0
    adverse_shrinking: float = 0.0

    prev_momentum_score: float = 0.0
    max_momentum_score: float = 0.0
    momentum_decay_detected: bool = False
    continuation_recovery_detected: bool = False
    continuation_weakness_ticks: int = 0

    bearish_accumulation_score: float = 0.0
    bearish_accumulation_ticks: int = 0
    favorable_fade_persistence: float = 0.0

    short_bearish_noise: bool = False
    short_favorable_fade_noise: bool = False
    momentum_hold_active: bool = False
    momentum_loss_streak: int = 0

    momentum_hold_events: int = 0
    momentum_exit_signals: int = 0
    fixed_time_proxy_fired: bool = False

    def _norm(self, ticks: int) -> float:
        if ticks <= 0:
            return 0.0
        return min(1.0, float(ticks) / MOMENTUM_DURATION_SCALE)

    def update_from_runtime(self, rt: "MicrostructureRuntime") -> None:
        self.eval_tick_count += 1
        dw = rt.duration_engine
        bc = rt.continuation_engine
        trans = rt.transition_engine

        bear_dur = trans.bearish_duration_ticks
        self.short_bearish_noise = 0 < bear_dur <= SHORT_BEARISH_NOISE_TICKS
        self.short_favorable_fade_noise = (
            0 < rt.favorable_fade_ticks <= SHORT_FAVORABLE_FADE_TICKS
            and rt.momentum_negative_streak < 2
        )

        pos_mom = dw.momentum_weighted
        if rt.momentum_negative_streak == 0:
            pos_mom = max(pos_mom, 0.42)
        self.positive_momentum_persistence = pos_mom

        self.favorable_continuation = dw.favorable_weighted
        self.bullish_duration_component = self._norm(bc.max_continuation_duration)

        adv_shrink = 1.0 - min(1.0, dw.adverse_weighted)
        if rt.adverse_persistence_count <= 2:
            adv_shrink = min(1.0, adv_shrink + 0.15)
        self.adverse_shrinking = adv_shrink

        self.momentum_continuation_score = min(
            1.0,
            0.40 * dw.momentum_weighted
            + 0.28 * self.favorable_continuation
            + 0.20 * self.bullish_duration_component
            + 0.12 * self.adverse_shrinking,
        )

        if (
            self.momentum_continuation_score >= MOMENTUM_HOLD_SCORE - 0.06
            and dw.momentum_weighted >= 0.32
        ):
            self.momentum_continuation_duration += 1
        else:
            self.momentum_continuation_duration = max(0, self.momentum_continuation_duration - 1)

        self.max_momentum_continuation_duration = max(
            self.max_momentum_continuation_duration, self.momentum_continuation_duration
        )
        self.max_momentum_score = max(self.max_momentum_score, self.momentum_continuation_score)

        if (
            self.prev_momentum_score < MOMENTUM_LOSS_SCORE
            and self.momentum_continuation_score >= MOMENTUM_HOLD_SCORE
        ):
            self.continuation_recovery_detected = True

        fade_persist = rt.favorable_fade_ticks >= 3 and not self.short_favorable_fade_noise
        self.favorable_fade_persistence = min(1.0, rt.favorable_fade_ticks / 8.0) if fade_persist else 0.0

        if (
            self.max_momentum_score >= MOMENTUM_HOLD_SCORE + 0.10
            and self.momentum_continuation_score
            < self.max_momentum_score - MOMENTUM_DECAY_DROP
        ):
            mom_lost = dw.momentum_weighted < 0.28 or rt.momentum_negative_streak >= 2
            if mom_lost or fade_persist or bc.continuation_decay_detected:
                self.momentum_decay_detected = True

        effective_bear = bear_dur if bear_dur > SHORT_BEARISH_NOISE_TICKS else 0
        if effective_bear > 0 and dw.bearish_weighted_score >= 0.32:
            self.bearish_accumulation_ticks += 1
            self.bearish_accumulation_score = min(
                1.0,
                self.bearish_accumulation_score + 0.11 * dw.bearish_weighted_score,
            )
        else:
            self.bearish_accumulation_ticks = max(0, self.bearish_accumulation_ticks - 1)
            self.bearish_accumulation_score = max(0.0, self.bearish_accumulation_score - 0.06)

        if self.momentum_continuation_score < WEAKNESS_SCORE:
            self.continuation_weakness_ticks += 1
        else:
            self.continuation_weakness_ticks = max(0, self.continuation_weakness_ticks - 1)

        if self.momentum_continuation_score < MOMENTUM_LOSS_SCORE:
            self.momentum_loss_streak += 1
        else:
            self.momentum_loss_streak = 0

        self.momentum_hold_active = self.should_hold_momentum()
        self.prev_momentum_score = self.momentum_continuation_score

        if self.eval_tick_count == 12 and dw.momentum_weighted < 0.22:
            self.fixed_time_proxy_fired = True

    def should_hold_momentum(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.momentum_continuation_score >= MOMENTUM_HOLD_SCORE:
            return True
        if (
            self.momentum_continuation_duration >= 4
            and self.momentum_continuation_score >= 0.40
        ):
            return True
        if self.short_bearish_noise and self.momentum_continuation_score >= 0.38:
            return True
        if self.short_favorable_fade_noise and self.momentum_continuation_score >= 0.42:
            return True
        if self.favorable_continuation >= 0.35 and self.positive_momentum_persistence >= 0.30:
            return True
        if (
            self.bullish_duration_component >= 0.35
            and self.momentum_continuation_score >= 0.36
        ):
            return True
        return False

    def momentum_loss_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.short_bearish_noise:
            return False
        return (
            self.max_momentum_continuation_duration >= 5
            and self.momentum_loss_streak >= MOMENTUM_LOSS_TICKS
            and self.momentum_continuation_score < MOMENTUM_LOSS_SCORE
        )

    def momentum_decay_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.momentum_decay_detected
            and self.favorable_fade_persistence >= 0.25
            and self.momentum_continuation_score < MOMENTUM_HOLD_SCORE - 0.05
        )

    def bearish_accumulation_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.short_bearish_noise:
            return False
        return (
            self.bearish_accumulation_score >= BEARISH_ACCUM_EXIT
            and self.bearish_accumulation_ticks >= BEARISH_ACCUM_TICKS
            and self.momentum_continuation_score < MOMENTUM_HOLD_SCORE - 0.06
        )

    def weakness_accumulation_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.continuation_weakness_ticks >= WEAKNESS_ACCUM_TICKS
            and self.max_momentum_continuation_duration >= 4
            and not self.should_hold_momentum()
        )

    def finalize_dict(self) -> dict[str, Any]:
        return {
            "momentum_continuation_score": round(self.momentum_continuation_score, 4),
            "momentum_continuation_duration": self.momentum_continuation_duration,
            "max_momentum_continuation_duration": self.max_momentum_continuation_duration,
            "favorable_continuation": round(self.favorable_continuation, 4),
            "positive_momentum_persistence": round(self.positive_momentum_persistence, 4),
            "bullish_duration_component": round(self.bullish_duration_component, 4),
            "adverse_shrinking": round(self.adverse_shrinking, 4),
            "momentum_decay_detected": self.momentum_decay_detected,
            "continuation_recovery_detected": self.continuation_recovery_detected,
            "continuation_weakness_ticks": self.continuation_weakness_ticks,
            "bearish_accumulation_score": round(self.bearish_accumulation_score, 4),
            "bearish_accumulation_ticks": self.bearish_accumulation_ticks,
            "favorable_fade_persistence": round(self.favorable_fade_persistence, 4),
            "short_bearish_noise": self.short_bearish_noise,
            "short_favorable_fade_noise": self.short_favorable_fade_noise,
            "momentum_hold_active_final": self.momentum_hold_active,
            "momentum_hold_events": self.momentum_hold_events,
            "fixed_time_proxy_fired": self.fixed_time_proxy_fired,
        }

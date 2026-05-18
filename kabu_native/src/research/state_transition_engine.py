"""
Phase 32: Persistence state transition engine (Logic Lab).

Market structure via state flow (bullish / neutral / bearish), not instant thresholds alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from research.state_persistence_engine import StatePersistenceEngine

# Global transition thresholds (not per symbol/day/time)
STATE_SCORE_MARGIN = 0.06
BULLISH_STATE_MIN = 0.50
BEARISH_STATE_MIN = 0.50
MIN_WARMUP_TICKS = 3
COLLAPSE_BEARISH_TICKS = 5
RECOVERY_PATH_MAX_TICKS = 18
BEARISH_LOCK_TICKS = 6
BULLISH_STABILIZE_TICKS = 4
NEUTRAL_STABILIZE_TICKS = 3
RECOVERY_FAILURE_BEAR_TICKS = 4


@dataclass
class StateTransitionEngine:
    """Tracks state flow, recovery/collapse paths, and transition velocities."""

    eval_tick_count: int = 0
    market_state: str = "neutral"
    prev_market_state: str = "neutral"

    bullish_duration_ticks: int = 0
    bearish_duration_ticks: int = 0
    neutral_duration_ticks: int = 0
    max_bullish_duration: int = 0
    max_bearish_duration: int = 0
    max_neutral_duration: int = 0

    collapse_phase: str = "none"
    collapse_bearish_ticks: int = 0
    collapse_transition_score: float = 0.0
    collapse_ready: bool = False

    recovery_phase: str = "none"
    recovery_path_ticks: int = 0
    recovery_transition_score: float = 0.0
    recovery_transition_active: bool = False
    recovery_transition_complete: bool = False
    recovery_failure: bool = False

    bearish_locked: bool = False
    bullish_to_bearish_velocity: Optional[float] = None
    _b2b_start_tick: Optional[int] = None

    transition_paths: list[str] = field(default_factory=list)
    transition_path_counts: dict[str, int] = field(default_factory=dict)
    transition_hold_events: int = 0
    transition_exit_signals: int = 0
    fixed_time_proxy_fired: bool = False

    def _record_path(self, path: str) -> None:
        self.transition_paths.append(path)
        if len(self.transition_paths) > 48:
            self.transition_paths = self.transition_paths[-48:]
        self.transition_path_counts[path] = self.transition_path_counts.get(path, 0) + 1

    def _instant_state(self, eng: "StatePersistenceEngine") -> str:
        bull = eng.bullish_instant
        bear = eng.bearish_instant
        if bull >= BULLISH_STATE_MIN and bull > bear + STATE_SCORE_MARGIN:
            return "bullish"
        if bear >= BEARISH_STATE_MIN and bear > bull + STATE_SCORE_MARGIN:
            return "bearish"
        return "neutral"

    def update_from_persistence(self, eng: "StatePersistenceEngine") -> None:
        self.eval_tick_count += 1
        self.prev_market_state = self.market_state
        self.market_state = self._instant_state(eng)

        if self.market_state == "bullish":
            self.bullish_duration_ticks += 1
            self.bearish_duration_ticks = 0
            self.neutral_duration_ticks = 0
        elif self.market_state == "bearish":
            self.bearish_duration_ticks += 1
            self.bullish_duration_ticks = 0
            self.neutral_duration_ticks = 0
        else:
            self.neutral_duration_ticks += 1
            self.bullish_duration_ticks = 0
            self.bearish_duration_ticks = 0

        self.max_bullish_duration = max(self.max_bullish_duration, self.bullish_duration_ticks)
        self.max_bearish_duration = max(self.max_bearish_duration, self.bearish_duration_ticks)
        self.max_neutral_duration = max(self.max_neutral_duration, self.neutral_duration_ticks)

        if self.prev_market_state != self.market_state:
            path = f"{self.prev_market_state}_to_{self.market_state}"
            self._record_path(path)

        self._update_collapse_path()
        self._update_recovery_path()
        self._update_bearish_lock()
        self._update_scores(eng)

        if self.eval_tick_count == 12 and self.market_state == "bearish" and eng.bearish_instant >= 0.55:
            self.fixed_time_proxy_fired = True

    def _update_collapse_path(self) -> None:
        if self.market_state == "bullish":
            self.collapse_phase = "none"
            self.collapse_bearish_ticks = 0
            return

        if self.prev_market_state == "bullish" and self.market_state == "neutral":
            self.collapse_phase = "bull_neutral"
            self._record_path("collapse_bull_neutral")
            return

        if self.collapse_phase == "bull_neutral" and self.market_state == "bearish":
            self.collapse_phase = "neutral_bearish"
            self.collapse_bearish_ticks = 1
            self._record_path("collapse_neutral_bearish")
            return

        if self.collapse_phase == "neutral_bearish" and self.market_state == "bearish":
            self.collapse_bearish_ticks += 1
        elif self.collapse_phase == "neutral_bearish" and self.market_state != "bearish":
            self.collapse_phase = "none"
            self.collapse_bearish_ticks = 0

        self.collapse_ready = (
            self.collapse_phase == "neutral_bearish"
            and self.collapse_bearish_ticks >= COLLAPSE_BEARISH_TICKS
        )

    def _update_recovery_path(self) -> None:
        if self.market_state == "bearish":
            if self.recovery_phase == "none":
                self.recovery_phase = "in_bearish"
                self.recovery_path_ticks = 1
            elif self.recovery_phase in ("in_bearish", "bear_neutral"):
                self.recovery_path_ticks += 1
            return

        if self.recovery_phase == "in_bearish" and self.market_state == "neutral":
            self.recovery_phase = "bear_neutral"
            self._record_path("recovery_bear_neutral")
            self.recovery_path_ticks += 1
            return

        if self.recovery_phase == "bear_neutral" and self.market_state == "bullish":
            self.recovery_phase = "complete"
            self.recovery_transition_complete = True
            self.recovery_transition_active = True
            self._record_path("recovery_neutral_bullish")
            if self.recovery_path_ticks > 0:
                self.bullish_to_bearish_velocity = float(self.recovery_path_ticks)
            return

        if self.recovery_phase in ("in_bearish", "bear_neutral"):
            self.recovery_path_ticks += 1
            if self.recovery_path_ticks > RECOVERY_PATH_MAX_TICKS:
                self.recovery_phase = "failed"
                self.recovery_failure = True
                self.recovery_transition_active = False

        if self.recovery_phase == "complete" and self.market_state == "bullish":
            self.recovery_transition_active = True

        if self.recovery_phase == "complete" and self.market_state == "bearish":
            self.recovery_failure = True
            self.recovery_transition_active = False
            self.recovery_phase = "failed"

    def _update_bearish_lock(self) -> None:
        direct_b2b = (
            self.prev_market_state == "bullish"
            and self.market_state == "bearish"
        )
        if direct_b2b:
            self._b2b_start_tick = self.eval_tick_count
            self._record_path("bullish_to_bearish_direct")

        if self.collapse_ready or direct_b2b:
            if self.bearish_duration_ticks >= BEARISH_LOCK_TICKS:
                if self.bullish_duration_ticks < 2:
                    self.bearish_locked = True

        if self.market_state == "bullish" and self.bullish_duration_ticks >= 3:
            self.bearish_locked = False
            self._b2b_start_tick = None

        if self._b2b_start_tick is not None and self.market_state == "bearish":
            vel = self.eval_tick_count - self._b2b_start_tick
            if vel > 0:
                self.bullish_to_bearish_velocity = float(vel)

    def _update_scores(self, eng: "StatePersistenceEngine") -> None:
        self.collapse_transition_score = min(
            1.0,
            (self.collapse_bearish_ticks / max(COLLAPSE_BEARISH_TICKS, 1)) * 0.5
            + (1.0 if self.collapse_ready else 0.0) * 0.5,
        )
        rec_prog = 0.0
        if self.recovery_phase == "bear_neutral":
            rec_prog = 0.5
        elif self.recovery_phase == "complete":
            rec_prog = 1.0
        elif self.recovery_phase == "in_bearish":
            rec_prog = 0.25
        self.recovery_transition_score = min(1.0, rec_prog)

    def should_hold(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        if self.recovery_transition_active:
            return True
        if self.bullish_duration_ticks >= BULLISH_STABILIZE_TICKS:
            return True
        if (
            self.neutral_duration_ticks >= NEUTRAL_STABILIZE_TICKS
            and self.recovery_phase in ("bear_neutral", "complete")
        ):
            return True
        return False

    def collapse_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return self.collapse_ready and self.bearish_locked

    def recovery_failure_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.recovery_failure
            and self.bearish_duration_ticks >= RECOVERY_FAILURE_BEAR_TICKS
            and not self.recovery_transition_active
        )

    def bearish_continuation_exit_ready(self) -> bool:
        if self.eval_tick_count < MIN_WARMUP_TICKS:
            return False
        return (
            self.bearish_locked
            and self.bearish_duration_ticks >= BEARISH_LOCK_TICKS
            and self.market_state == "bearish"
            and not self.recovery_transition_active
        )

    def finalize_dict(self) -> dict[str, Any]:
        return {
            "transition_market_state": self.market_state,
            "max_bullish_duration_ticks": self.max_bullish_duration,
            "max_bearish_duration_ticks": self.max_bearish_duration,
            "max_neutral_duration_ticks": self.max_neutral_duration,
            "collapse_transition_score": round(self.collapse_transition_score, 4),
            "recovery_transition_score": round(self.recovery_transition_score, 4),
            "collapse_transition_ready": self.collapse_ready,
            "recovery_transition_active": self.recovery_transition_active,
            "recovery_transition_complete": self.recovery_transition_complete,
            "recovery_transition_failure": self.recovery_failure,
            "bearish_locked": self.bearish_locked,
            "bullish_to_bearish_velocity_ticks": self.bullish_to_bearish_velocity,
            "transition_paths": list(self.transition_paths),
            "transition_path_frequency": dict(self.transition_path_counts),
            "transition_hold_events": self.transition_hold_events,
            "transition_exit_signals": self.transition_exit_signals,
            "fixed_time_proxy_fired": self.fixed_time_proxy_fired,
        }

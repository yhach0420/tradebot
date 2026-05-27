"""
Phase 135: Fade-exit switch cooldown shadow (review / replay only).

After momentum_fade_exit / price_momentum_fade_exit, block cross-symbol entries until
any event-based release signal (no fixed-time cooldown).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.fade_watch_shadow import (
    FADE_WATCH_TRIGGER_REASONS,
    FadeWatchState,
    GIVEBACK_FRAC,
    MOMENTUM_EPS,
    PNL_EPS,
    _pnl,
    _reaccel_score,
)
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1

POLICY_FADE_SWITCH_COOLDOWN_SHADOW = "fade_switch_cooldown_shadow"

FADE_SWITCH_TRIGGER_REASONS = FADE_WATCH_TRIGGER_REASONS
# Event-count gate (not wall-clock): require ticks on faded symbol before release signals count.
MIN_COOLDOWN_OBSERVATION_TICKS = 2


def uses_fade_switch_cooldown_shadow(policy: str) -> bool:
    return policy == POLICY_FADE_SWITCH_COOLDOWN_SHADOW


def cfg_for_v1_exits(cfg: Any) -> Any:
    """Keep combined_structural_exit_v1 exits; shadow only gates post-fade switches."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_fade_switch_cooldown_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()


@dataclass
class FadeSwitchCooldownState:
    """Tracks post-fade symbol path until switch is allowed."""

    symbol: str
    fade_exit_time: str
    fade_exit_ts: float
    fade_exit_reason: str
    entry_price: float
    fade_price: float
    fade_momentum: Optional[float] = None
    fade_pnl: float = 0.0
    peak_price: float = 0.0
    peak_pnl: float = 0.0
    post_low: float = 0.0
    ticks_observed: int = 0
    released: bool = False
    release_reason: str = ""
    new_high_after_fade: bool = False
    new_mfe_created: bool = False
    momentum_recovery: bool = False
    giveback_exceeded: bool = False
    breakdown_detected: bool = False
    last_signals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def enter(
        cls,
        *,
        symbol: str,
        fade_exit_time: str,
        fade_exit_ts: float,
        fade_exit_reason: str,
        entry_price: float,
        fade_price: float,
        fade_momentum: Optional[float],
        fade_pnl: float,
    ) -> FadeSwitchCooldownState:
        return cls(
            symbol=symbol,
            fade_exit_time=fade_exit_time,
            fade_exit_ts=fade_exit_ts,
            fade_exit_reason=fade_exit_reason,
            entry_price=entry_price,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            fade_pnl=fade_pnl,
            peak_price=fade_price,
            peak_pnl=max(fade_pnl, _pnl(entry_price, fade_price)),
            post_low=fade_price,
        )


def cooldown_log_fields(state: FadeSwitchCooldownState) -> dict[str, Any]:
    return {
        "fade_switch_cooldown_symbol": state.symbol,
        "fade_switch_cooldown_released": state.released,
        "fade_switch_release_reason": state.release_reason,
        "fade_switch_ticks_observed": state.ticks_observed,
        "new_high_after_fade": state.new_high_after_fade,
        "new_mfe_created": state.new_mfe_created,
        "momentum_recovery": state.momentum_recovery,
        "giveback_exceeded": state.giveback_exceeded,
        "breakdown_detected": state.breakdown_detected,
        **state.last_signals,
    }


def process_fade_switch_cooldown_tick(
    state: FadeSwitchCooldownState,
    *,
    price: float,
    momentum: Optional[float],
    ts: float,
) -> Optional[str]:
    """
    Update cooldown state. Return release_reason when switch may proceed; None while blocked.
    """
    if state.released:
        return state.release_reason

    state.ticks_observed += 1
    entry_price = state.entry_price
    pnl = _pnl(entry_price, price)

    prev_peak = state.peak_price
    if price > state.peak_price:
        state.peak_price = price
    if price < state.post_low:
        state.post_low = price

    new_high_tick = price > prev_peak + 1e-9
    if new_high_tick and price > state.fade_price:
        state.new_high_after_fade = True

    mfe_updated_tick = pnl > state.peak_pnl + 1e-9
    if mfe_updated_tick:
        state.peak_pnl = pnl
        state.new_mfe_created = True

    momentum_recovery = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum > state.fade_momentum + MOMENTUM_EPS
    )
    state.momentum_recovery = momentum_recovery

    giveback_exceeded = state.peak_pnl > PNL_EPS and pnl <= state.peak_pnl * (1.0 - GIVEBACK_FRAC)
    state.giveback_exceeded = giveback_exceeded

    momentum_down = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum < state.fade_momentum - MOMENTUM_EPS
    )
    breakdown = (
        price < state.fade_price - 1e-9
        and price <= state.post_low + 1e-9
        and state.post_low < state.fade_price
    ) or (
        price < state.fade_price and not state.new_mfe_created and momentum_down
    )
    state.breakdown_detected = breakdown

    state.last_signals = {
        "new_high_after_fade": state.new_high_after_fade,
        "new_mfe_created": state.new_mfe_created,
        "momentum_recovery": momentum_recovery,
        "giveback_exceeded": giveback_exceeded,
        "breakdown_detected": breakdown,
        "pnl_at_tick": round(pnl, 4),
    }

    if state.ticks_observed < MIN_COOLDOWN_OBSERVATION_TICKS:
        return None

    # Release switch block when any event signal fires (event-driven, not time-based).
    if breakdown:
        state.released = True
        state.release_reason = "breakdown_detected"
    elif state.new_high_after_fade:
        state.released = True
        state.release_reason = "new_high_after_fade"
    elif mfe_updated_tick:
        state.released = True
        state.release_reason = "new_mfe_created"
    elif momentum_recovery:
        state.released = True
        state.release_reason = "momentum_recovery"
    elif giveback_exceeded:
        state.released = True
        state.release_reason = "giveback_exceeded"

    return state.release_reason if state.released else None


def cross_symbol_switch_blocked(
    cooldowns: Mapping[str, FadeSwitchCooldownState],
    *,
    new_symbol: str,
) -> tuple[bool, Optional[str]]:
    """True if any active cooldown on a different symbol has not released."""
    for sym, state in cooldowns.items():
        if sym != new_symbol and not state.released:
            return True, sym
    return False, None

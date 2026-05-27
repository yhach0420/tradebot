"""
Phase 127: Shadow-only state-based fade_watch exit (event-driven, no fixed-time exit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
)

POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW = (
    "combined_structural_exit_v1_fade_watch_shadow"
)

FADE_WATCH_TRIGGER_REASONS = frozenset(
    {"momentum_fade_exit", "price_momentum_fade_exit"}
)

FADE_WATCH_EXIT_REASONS = frozenset(
    {
        "fade_watch_exit",
        "fade_watch_breakdown",
        "fade_watch_giveback",
        "fade_watch_momentum_fade",
        "fade_watch_reacceleration_hold",
        "fade_watch_session_close",
    }
)

REVIEW_EXIT_ALIASES = frozenset(
    {
        *FADE_WATCH_EXIT_REASONS,
        "morning_session_close",
        "afternoon_session_close",
        "session_end",
    }
)

GIVEBACK_FRAC = 0.25
MOMENTUM_EPS = 0.02
REACCEL_MIN_SIGNALS = 2
PNL_EPS = 0.01

_INTERNAL_TO_REVIEW_REASON = {
    "giveback_exceeded": "fade_watch_giveback",
    "breakdown_detected": "fade_watch_breakdown",
    "no_new_high_and_momentum_down": "fade_watch_momentum_fade",
    "observation_window_end": "fade_watch_session_close",
    "session_end": "fade_watch_session_close",
    "morning_session_close": "morning_session_close",
    "afternoon_session_close": "afternoon_session_close",
}


def uses_fade_watch_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW


def is_fade_watch_review_reason(reason: str) -> bool:
    r = str(reason or "").strip()
    return r in REVIEW_EXIT_ALIASES or r in FADE_WATCH_EXIT_REASONS


@dataclass
class FadeWatchState:
    entered: bool = False
    entry_time: str = ""
    entry_ts: float = 0.0
    initial_reason: str = ""
    fade_price: float = 0.0
    fade_momentum: Optional[float] = None
    mfe_at_fade: float = 0.0
    peak_price: float = 0.0
    peak_pnl: float = 0.0
    post_low: float = 0.0
    new_high_since_fade: bool = False
    mfe_updated_since_fade: bool = False
    reacceleration_detected: bool = False
    ticks_in_watch: int = 0
    fade_watch_hold_sec: float = 0.0
    last_signals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def enter(
        cls,
        *,
        entry_time: str,
        entry_ts: float,
        initial_reason: str,
        fade_price: float,
        fade_momentum: Optional[float],
        mfe_at_fade: float,
        entry_price: float,
    ) -> FadeWatchState:
        pnl = _pnl(entry_price, fade_price)
        return cls(
            entered=True,
            entry_time=entry_time,
            entry_ts=entry_ts,
            initial_reason=initial_reason,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            mfe_at_fade=max(mfe_at_fade, pnl),
            peak_price=fade_price,
            peak_pnl=max(mfe_at_fade, pnl),
            post_low=fade_price,
        )


def _pnl(entry_price: float, price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return round((price - entry_price) / entry_price * 100.0, 4)


def _reaccel_score(
    *,
    price: float,
    fade_price: float,
    pnl: float,
    peak_pnl: float,
    momentum: Optional[float],
    fade_momentum: Optional[float],
    new_high_tick: bool,
    mfe_updated_tick: bool,
    vwap_above: Optional[bool],
) -> int:
    score = 0
    if price > fade_price:
        score += 1
    if new_high_tick:
        score += 1
    if mfe_updated_tick:
        score += 1
    if momentum is not None and fade_momentum is not None and momentum > fade_momentum + MOMENTUM_EPS:
        score += 1
    if vwap_above is True:
        score += 1
    return score


def fade_watch_log_fields(state: FadeWatchState) -> dict[str, Any]:
    sig = state.last_signals
    return {
        "fade_watch_entered": state.entered,
        "fade_watch_entry_time": state.entry_time,
        "fade_watch_initial_reason": state.initial_reason,
        "fade_watch_exit_reason": sig.get("fade_watch_exit_reason", ""),
        "reacceleration_detected": state.reacceleration_detected or bool(sig.get("reacceleration_detected")),
        "new_high_after_fade": state.new_high_since_fade or bool(sig.get("new_high_after_fade")),
        "new_mfe_created": state.mfe_updated_since_fade or bool(sig.get("new_mfe_created")),
        "momentum_recovery": bool(sig.get("momentum_recovery")),
        "giveback_exceeded": bool(sig.get("giveback_exceeded")),
        "breakdown_detected": bool(sig.get("breakdown_detected")),
        "fade_watch_hold_sec": round(state.fade_watch_hold_sec, 1),
    }


def process_fade_watch_tick(
    state: FadeWatchState,
    *,
    entry_price: float,
    price: float,
    momentum: Optional[float],
    ts: float,
    vwap_above: Optional[bool] = None,
    mode: str = "state_based",
) -> Optional[tuple[str, dict[str, Any]]]:
    """Return (review_exit_reason, signal_log) when exit; None to continue holding."""
    state.ticks_in_watch += 1
    state.fade_watch_hold_sec = max(0.0, ts - state.entry_ts)
    pnl = _pnl(entry_price, price)

    prev_peak = state.peak_price
    if price > state.peak_price:
        state.peak_price = price
    if price < state.post_low:
        state.post_low = price

    new_high_tick = price > prev_peak + 1e-9
    if new_high_tick and price > state.fade_price:
        state.new_high_since_fade = True

    mfe_updated_tick = pnl > state.peak_pnl + 1e-9
    if mfe_updated_tick:
        state.peak_pnl = pnl
        state.mfe_updated_since_fade = True

    momentum_recovery = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum > state.fade_momentum + MOMENTUM_EPS
    )
    reaccel_score = _reaccel_score(
        price=price,
        fade_price=state.fade_price,
        pnl=pnl,
        peak_pnl=state.peak_pnl,
        momentum=momentum,
        fade_momentum=state.fade_momentum,
        new_high_tick=new_high_tick,
        mfe_updated_tick=mfe_updated_tick,
        vwap_above=vwap_above,
    )
    reacceleration_detected = reaccel_score >= REACCEL_MIN_SIGNALS
    if reacceleration_detected:
        state.reacceleration_detected = True

    giveback_exceeded = state.peak_pnl > PNL_EPS and pnl <= state.peak_pnl * (1.0 - GIVEBACK_FRAC)
    momentum_down = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum < state.fade_momentum - MOMENTUM_EPS
    )
    no_new_high_momentum_down = (not state.new_high_since_fade) and momentum_down
    breakdown = (
        price < state.fade_price - 1e-9
        and price <= state.post_low + 1e-9
        and state.post_low < state.fade_price
    ) or (
        price < state.fade_price and not state.mfe_updated_since_fade and momentum_down
    )
    if vwap_above is False and price < state.fade_price:
        breakdown = True

    state.last_signals = {
        "reacceleration_detected": reacceleration_detected,
        "new_high_after_fade": state.new_high_since_fade,
        "new_mfe_created": state.mfe_updated_since_fade,
        "momentum_recovery": momentum_recovery,
        "giveback_exceeded": giveback_exceeded,
        "breakdown_detected": breakdown,
    }

    if reacceleration_detected:
        state.last_signals["fade_watch_exit_reason"] = "fade_watch_reacceleration_hold"
        return None

    internal_reason: Optional[str] = None
    if mode == "giveback_only":
        if giveback_exceeded:
            internal_reason = "giveback_exceeded"
    elif mode == "reaccel_giveback":
        if breakdown:
            internal_reason = "breakdown_detected"
        elif giveback_exceeded and not reacceleration_detected:
            internal_reason = "giveback_exceeded"
        elif no_new_high_momentum_down and not state.reacceleration_detected:
            internal_reason = "no_new_high_and_momentum_down"
    else:
        if breakdown:
            internal_reason = "breakdown_detected"
        elif giveback_exceeded:
            internal_reason = "giveback_exceeded"
        elif no_new_high_momentum_down:
            internal_reason = "no_new_high_and_momentum_down"

    if internal_reason is None:
        return None

    review_reason = _INTERNAL_TO_REVIEW_REASON.get(internal_reason, "fade_watch_exit")
    state.last_signals["fade_watch_exit_reason"] = review_reason
    log = fade_watch_log_fields(state)
    return review_reason, log


def map_session_close_reason(reason: str) -> str:
    r = str(reason or "session_end")
    if r in ("morning_session_close", "afternoon_session_close"):
        return r
    return _INTERNAL_TO_REVIEW_REASON.get(r, "fade_watch_session_close")


def _cfg_for_v1_signal(cfg: Any) -> Any:
    """Shadow policy uses v1 exit rules to detect fade triggers."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_fade_watch_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()


def combined_exit_or_fade_watch_trigger(
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
) -> Optional[tuple[str, float, float, str]]:
    """Return (kind, pnl, price, reason) where kind is 'exit' or 'fade_watch'."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    sig = combined_exit_signal_on_latest_tick(
        rich_ticks, entry_price, _cfg_for_v1_signal(cfg)
    )
    if sig is None:
        return None
    pnl, reason, close_px = sig
    if uses_fade_watch_shadow(policy) and reason in FADE_WATCH_TRIGGER_REASONS:
        return ("fade_watch", pnl, close_px, reason)
    return ("exit", pnl, close_px, reason)

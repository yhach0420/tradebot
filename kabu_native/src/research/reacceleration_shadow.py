"""
Phase 131: Reacceleration shadow — gated fade deferral (review / replay only).

Gate: mfe_pct > 0.15 AND NOT breakdown_at_fade → defer fade exit, continue until
breakdown / giveback / fade_price_break / session_close. Event-driven only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.range_hold_exit_review import (
    FADE_PRICE_BREAK_EPS,
    RECENT_LOW_BREAK_EPS,
    _breakdown_on_tick,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
)
from research.fade_watch_shadow import (
    FADE_WATCH_TRIGGER_REASONS,
    FadeWatchState,
    GIVEBACK_FRAC,
    MOMENTUM_EPS,
    PNL_EPS,
    _pnl,
    _reaccel_score,
    fade_watch_log_fields,
    map_session_close_reason,
)

POLICY_REACCELERATION_SHADOW = "reacceleration_shadow"
MFE_GATE = 0.15

REACCEL_EXIT_REASONS = frozenset(
    {
        "reaccel_shadow_breakdown",
        "reaccel_shadow_giveback",
        "reaccel_shadow_fade_price_break",
        "reaccel_shadow_session_close",
        "reaccel_shadow_momentum_fade",
    }
)


def uses_reacceleration_shadow(policy: str) -> bool:
    return policy == POLICY_REACCELERATION_SHADOW


def _mfe_from_ticks(rich_ticks: Sequence[Mapping[str, Any]], entry_price: float) -> float:
    if not rich_ticks or entry_price <= 0:
        return 0.0
    return max(_pnl(entry_price, float(t.get("price") or entry_price)) for t in rich_ticks)


def _recent_low_from_ticks(rich_ticks: Sequence[Mapping[str, Any]]) -> float:
    if not rich_ticks:
        return 0.0
    return min(float(t.get("price") or 0) for t in rich_ticks if float(t.get("price") or 0) > 0)


def breakdown_at_fade(
    rich_ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    fade_price: float,
    fade_pnl: float,
    fade_momentum: Optional[float],
) -> bool:
    if not rich_ticks:
        return False
    recent_low = _recent_low_from_ticks(rich_ticks)
    peak_pnl = _mfe_from_ticks(rich_ticks, entry_price)
    return _breakdown_on_tick(
        px=fade_price,
        pnl=fade_pnl,
        mom=fade_momentum,
        fade_momentum=fade_momentum,
        fade_price=fade_price,
        recent_low=recent_low if recent_low > 0 else fade_price,
        peak_pnl=peak_pnl,
        post_low=fade_price,
        prev_post_low=fade_price,
        new_high_since_fade=False,
    )


def _cfg_for_v1_signal(cfg: Any) -> Any:
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    if not uses_reacceleration_shadow(policy):
        return cfg

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    return _Proxy()


def combined_exit_or_reacceleration_trigger(
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
) -> Optional[tuple[str, float, float, str]]:
    """
    Return (kind, pnl, price, reason):
      - ('exit', ...) immediate exit (non-fade or gate failed)
      - ('reaccel_extend', ...) defer fade — enter continuation state
    """
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    sig = combined_exit_signal_on_latest_tick(
        rich_ticks, entry_price, _cfg_for_v1_signal(cfg)
    )
    if sig is None:
        return None
    pnl, reason, close_px = sig
    if not uses_reacceleration_shadow(policy) or reason not in FADE_WATCH_TRIGGER_REASONS:
        return ("exit", pnl, close_px, reason)

    mfe = _mfe_from_ticks(rich_ticks, entry_price)
    fade_mom = float(rich_ticks[-1].get("momentum") or 0) if rich_ticks else None
    if mfe <= MFE_GATE:
        return ("exit", pnl, close_px, reason)
    if breakdown_at_fade(
        rich_ticks,
        entry_price=entry_price,
        fade_price=float(close_px),
        fade_pnl=float(pnl),
        fade_momentum=fade_mom,
    ):
        return ("exit", pnl, close_px, reason)

    return ("reaccel_extend", pnl, close_px, reason)


def process_reacceleration_tick(
    state: FadeWatchState,
    *,
    entry_price: float,
    price: float,
    momentum: Optional[float],
    ts: float,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Event-driven continuation / exit. No fixed-second wait."""
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
        vwap_above=None,
    )
    reacceleration_detected = reaccel_score >= 2
    if reacceleration_detected:
        state.reacceleration_detected = True

    giveback_exceeded = state.peak_pnl > PNL_EPS and pnl <= state.peak_pnl * (1.0 - GIVEBACK_FRAC)
    fade_price_break = price < state.fade_price * (1.0 - FADE_PRICE_BREAK_EPS)
    momentum_down = (
        momentum is not None
        and state.fade_momentum is not None
        and momentum < state.fade_momentum - MOMENTUM_EPS
    )
    breakdown = (
        price < state.fade_price - 1e-9
        and price <= state.post_low + 1e-9
        and state.post_low < state.fade_price
    ) or (fade_price_break and momentum_down and not state.new_high_since_fade)

    state.last_signals = {
        "reacceleration_detected": reacceleration_detected,
        "new_high_after_fade": state.new_high_since_fade,
        "new_mfe_created": state.mfe_updated_since_fade,
        "momentum_recovery": momentum_recovery,
        "giveback_exceeded": giveback_exceeded,
        "breakdown_detected": breakdown,
        "fade_price_break": fade_price_break,
    }

    # Continue while reacceleration signals active
    if reacceleration_detected or momentum_recovery or state.new_high_since_fade:
        if not (breakdown or giveback_exceeded or fade_price_break):
            state.last_signals["fade_watch_exit_reason"] = "reaccel_shadow_continue"
            return None

    if breakdown:
        reason = "reaccel_shadow_breakdown"
    elif giveback_exceeded:
        reason = "reaccel_shadow_giveback"
    elif fade_price_break:
        reason = "reaccel_shadow_fade_price_break"
    elif momentum_down and not state.new_high_since_fade:
        reason = "reaccel_shadow_momentum_fade"
    else:
        return None

    state.last_signals["fade_watch_exit_reason"] = reason
    return reason, fade_watch_log_fields(state)


def map_reaccel_session_close(reason: str) -> str:
    r = str(reason or "session_end")
    if r in ("morning_session_close", "afternoon_session_close", "session_end"):
        mapped = map_session_close_reason(r)
        if mapped != "fade_watch_session_close":
            return mapped
    return "reaccel_shadow_session_close"

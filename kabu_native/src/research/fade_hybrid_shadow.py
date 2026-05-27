"""
Phase 162: G hybrid fade shadow — watch state, breakdown confirmed, 2nd-fade exit.
Review / live shadow only; production combined_structural_exit_v1 unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from research.fade_exit_replay import FADE_EXIT_REASONS
from research.fade_watch_shadow import (
    FADE_WATCH_TRIGGER_REASONS,
    FadeWatchState,
    _pnl,
    fade_watch_log_fields,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
)

POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW = (
    "combined_structural_exit_v1_fade_hybrid_shadow"
)
POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW = (
    "combined_structural_exit_v1_breakdown_confirmed_shadow"
)
POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW = (
    "combined_structural_exit_v1_fade_breakdown_shadow"
)
POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW = (
    "combined_structural_exit_v1_fade_disable_shadow"
)

FADE_HYBRID_EXIT_REASONS = frozenset(
    {
        "fade_hybrid_breakdown",
        "fade_hybrid_second_fade",
        "fade_hybrid_structural_exit",
        "fade_hybrid_session_close",
        "fade_breakdown_confirmed",
        "fade_breakdown_structural_exit",
        "fade_watch_breakdown",
        "fade_watch_giveback",
        "fade_watch_momentum_fade",
        "fade_watch_session_close",
        "fade_watch_exit",
    }
)

HYBRID_REVIEW_REASONS = frozenset(
    {
        *FADE_HYBRID_EXIT_REASONS,
        *FADE_EXIT_REASONS,
        "morning_session_close",
        "afternoon_session_close",
        "session_end",
        "stop_hit",
        "quality_decay_exit",
        "favorable_fade_exit",
        "vwap_break_exit",
        "mfe_giveback_exit",
        "take_exit",
    }
)

GIVEBACK_SMALL_FRAC = 0.25
HIGH_ZONE_FRAC = 0.85
MOMENTUM_BREAKDOWN_MAX = 0.15
RANGE_NEAR_FRAC = 0.08


def uses_fade_hybrid_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW


def uses_breakdown_confirmed_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_BREAKDOWN_CONFIRMED_SHADOW


def uses_fade_breakdown_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW


def uses_fade_disable_shadow(policy: str) -> bool:
    return policy == POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW


def uses_fade_shadow_watch(policy: str) -> bool:
    from research.fade_watch_shadow import uses_fade_watch_shadow

    return (
        uses_fade_watch_shadow(policy)
        or uses_fade_hybrid_shadow(policy)
        or uses_fade_breakdown_shadow(policy)
    )


def uses_fade_shadow_trigger(policy: str) -> bool:
    return (
        uses_fade_shadow_watch(policy)
        or uses_breakdown_confirmed_shadow(policy)
        or uses_fade_disable_shadow(policy)
    )


def is_fade_hybrid_review_reason(reason: str) -> bool:
    r = str(reason or "").strip()
    return r in HYBRID_REVIEW_REASONS or r.startswith("fade_hybrid_")


@dataclass
class FadeHybridState(FadeWatchState):
    fade_hybrid_state: str = "fade_watch"
    fade_signal_count: int = 0
    take_reached_at_fade: bool = False
    pnl_at_fade: float = 0.0
    range_hold_protect_count: int = 0
    breakdown_confirmed_exit: bool = False
    second_fade_exit: bool = False
    last_signals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def enter_hybrid(
        cls,
        *,
        entry_time: str,
        entry_ts: float,
        initial_reason: str,
        fade_price: float,
        fade_momentum: Optional[float],
        mfe_at_fade: float,
        entry_price: float,
        take_reached: bool,
    ) -> FadeHybridState:
        base = cls.enter(
            entry_time=entry_time,
            entry_ts=entry_ts,
            initial_reason=initial_reason,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            mfe_at_fade=mfe_at_fade,
            entry_price=entry_price,
        )
        base.fade_hybrid_state = "fade_watch"
        base.fade_signal_count = 1
        base.take_reached_at_fade = take_reached
        base.pnl_at_fade = _pnl(entry_price, fade_price)
        return base


@dataclass
class FadeBreakdownState(FadeHybridState):
    """Fade deferred state: exit only when breakdown confirmed (no second-fade exit)."""

    fade_hybrid_state: str = "fade_breakdown_watch"
    second_fade_ignored_count: int = 0

    @classmethod
    def enter_breakdown(
        cls,
        *,
        entry_time: str,
        entry_ts: float,
        initial_reason: str,
        fade_price: float,
        fade_momentum: Optional[float],
        mfe_at_fade: float,
        entry_price: float,
        take_reached: bool,
    ) -> "FadeBreakdownState":
        base = cls.enter_hybrid(
            entry_time=entry_time,
            entry_ts=entry_ts,
            initial_reason=initial_reason,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            mfe_at_fade=mfe_at_fade,
            entry_price=entry_price,
            take_reached=take_reached,
        )
        base.fade_hybrid_state = "fade_breakdown_watch"
        base.second_fade_exit = False
        return base  # type: ignore[return-value]


def _peak_pnl_ticks(ticks: Sequence[Mapping[str, Any]]) -> float:
    return max((float(t.get("pnl_pct") or 0) for t in ticks), default=0.0)


def _new_low_since_fade(state: FadeHybridState, price: float) -> bool:
    return price < state.fade_price - 1e-9 and price <= state.post_low + 1e-9


def range_hold_protect_hybrid(
    *,
    pnl: float,
    peak_pnl: float,
    price: float,
    fade_price: float,
) -> bool:
    if pnl >= 0:
        return True
    if peak_pnl > 0.01:
        giveback = (peak_pnl - pnl) / peak_pnl
        if giveback < GIVEBACK_SMALL_FRAC:
            return True
        if pnl >= peak_pnl * HIGH_ZONE_FRAC:
            return True
    if fade_price > 0 and abs(price - fade_price) / fade_price * 100.0 <= RANGE_NEAR_FRAC:
        return True
    return False


def breakdown_confirmed_hybrid(
    *,
    momentum: Optional[float],
    pnl: float,
    take_reached_at_fade: bool,
    price: float,
    state: FadeHybridState,
) -> bool:
    if take_reached_at_fade:
        return False
    mom = float(momentum or 0)
    if mom >= MOMENTUM_BREAKDOWN_MAX:
        return False
    if pnl >= 0:
        return False
    if price < state.fade_price - 1e-9:
        return True
    if _new_low_since_fade(state, price):
        return True
    return False


def fade_hybrid_log_fields(state: FadeHybridState) -> dict[str, Any]:
    base = fade_watch_log_fields(state)
    entry_pnl = state.pnl_at_fade
    exit_pnl = float(state.last_signals.get("exit_pnl") or entry_pnl)
    return {
        **base,
        "fade_hybrid_state": state.fade_hybrid_state,
        "fade_watch_entered": state.entered,
        "fade_watch_exit": bool(state.last_signals.get("fade_watch_exit")),
        "fade_watch_exit_reason": state.last_signals.get("fade_watch_exit_reason", ""),
        "fade_watch_initial_reason": state.initial_reason,
        "fade_watch_duration_sec": round(state.fade_watch_hold_sec, 1),
        "fade_watch_pnl_delta": round(exit_pnl - entry_pnl, 4),
        "fade_watch_mfe_at_entry": round(state.mfe_at_fade, 4),
        "fade_watch_take_reached": state.take_reached_at_fade,
        "fade_watch_breakdown_confirmed": state.breakdown_confirmed_exit,
        "fade_watch_second_fade": state.second_fade_exit,
        "fade_watch_range_hold_protected": state.range_hold_protect_count > 0,
        "fade_signal_count": state.fade_signal_count,
    }


def fade_breakdown_log_fields(state: FadeBreakdownState) -> dict[str, Any]:
    base = fade_hybrid_log_fields(state)
    return {
        **base,
        "second_fade_ignored_count": state.second_fade_ignored_count,
        "fade_deferred": True,
    }


def _recent_low(rich_ticks: Sequence[Mapping[str, Any]], lookback: int = 6) -> Optional[float]:
    if len(rich_ticks) < 2:
        return None
    sub = rich_ticks[-lookback:]
    prices = [float(t.get("price") or 0) for t in sub if float(t.get("price") or 0) > 0]
    if len(prices) < 2:
        return None
    return min(prices[:-1])


def breakdown_confirmed_deferred(
    *,
    state: FadeBreakdownState,
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    price: float,
    momentum: Optional[float],
) -> bool:
    """Phase166 breakdown-confirmed for deferred fade (simple)."""
    if state.take_reached_at_fade:
        return False
    pnl = _pnl(entry_price, price)
    if pnl >= 0:
        return False
    mom = float(momentum or 0)
    if mom >= MOMENTUM_BREAKDOWN_MAX:
        return False
    rl = _recent_low(rich_ticks)
    if rl is not None and price < rl - 1e-9:
        return True
    if price < state.fade_price - 1e-9:
        return True
    return False


def process_fade_breakdown_tick(
    state: FadeBreakdownState,
    *,
    entry_price: float,
    price: float,
    momentum: Optional[float],
    ts: float,
    rich_ticks: Sequence[Mapping[str, Any]],
    cfg: Any,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Deferred fade: exit only on breakdown-confirmed (plus range-hold protect)."""
    state.ticks_in_watch += 1
    state.fade_watch_hold_sec = max(0.0, ts - state.entry_ts)
    pnl = _pnl(entry_price, price)
    state.peak_pnl = max(state.peak_pnl, pnl)
    state.peak_price = max(state.peak_price, price)
    state.post_low = min(state.post_low, price)

    sig = combined_exit_signal_on_latest_tick(rich_ticks, entry_price, _cfg_for_v1_signal(cfg))
    if sig is not None:
        sig_pnl, sig_reason, _ = sig
        if sig_reason not in FADE_WATCH_TRIGGER_REASONS:
            state.last_signals = {
                "fade_watch_exit": True,
                "fade_watch_exit_reason": "fade_breakdown_structural_exit",
                "exit_pnl": sig_pnl,
            }
            return "fade_breakdown_structural_exit", fade_breakdown_log_fields(state)
        # fade signal while deferred: ignored unless breakdown later
        if sig_reason in FADE_WATCH_TRIGGER_REASONS and state.fade_signal_count >= 1:
            state.second_fade_ignored_count += 1

    if range_hold_protect_hybrid(
        pnl=pnl,
        peak_pnl=state.peak_pnl,
        price=price,
        fade_price=state.fade_price,
    ):
        state.range_hold_protect_count += 1
        return None

    if breakdown_confirmed_deferred(
        state=state,
        rich_ticks=rich_ticks,
        entry_price=entry_price,
        price=price,
        momentum=momentum,
    ):
        state.breakdown_confirmed_exit = True
        state.last_signals = {
            "fade_watch_exit": True,
            "fade_watch_exit_reason": "fade_breakdown_confirmed",
            "exit_pnl": pnl,
        }
        return "fade_breakdown_confirmed", fade_breakdown_log_fields(state)

    return None


def _cfg_for_v1_signal(cfg: Any) -> Any:
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            if name == "structural_exit_policy":
                return POLICY_COMBINED_STRUCTURAL_EXIT_V1
            return getattr(cfg, name)

    if uses_fade_shadow_trigger(policy):
        return _Proxy()
    return cfg


def combined_exit_or_fade_shadow_trigger(
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
    *,
    take_reached: bool = False,
) -> Optional[tuple[str, float, float, str]]:
    """Return (kind, pnl, price, reason); kind is exit | fade_watch."""
    policy = str(getattr(cfg, "structural_exit_policy", "") or "")
    sig = combined_exit_signal_on_latest_tick(
        rich_ticks, entry_price, _cfg_for_v1_signal(cfg)
    )
    if sig is None:
        return None
    pnl, reason, close_px = sig

    if reason not in FADE_WATCH_TRIGGER_REASONS:
        return ("exit", pnl, close_px, reason)

    if uses_fade_disable_shadow(policy):
        return None

    if uses_breakdown_confirmed_shadow(policy):
        last = rich_ticks[-1]
        mom = float(last.get("momentum") or 0)
        px = float(last.get("price") or close_px)
        fake = FadeHybridState.enter_hybrid(
            entry_time=str(last.get("ts") or ""),
            entry_ts=float(last.get("ts_epoch") or 0),
            initial_reason=reason,
            fade_price=px,
            fade_momentum=mom,
            mfe_at_fade=pnl,
            entry_price=entry_price,
            take_reached=take_reached,
        )
        if breakdown_confirmed_hybrid(
            momentum=mom,
            pnl=pnl,
            take_reached_at_fade=take_reached,
            price=px,
            state=fake,
        ):
            return ("exit", pnl, close_px, reason)
        return None

    if uses_fade_hybrid_shadow(policy) or uses_fade_breakdown_shadow(policy) or uses_fade_shadow_watch(policy):
        from research.fade_watch_shadow import uses_fade_watch_shadow

        if uses_fade_hybrid_shadow(policy) or uses_fade_breakdown_shadow(policy) or uses_fade_watch_shadow(policy):
            return ("fade_watch", pnl, close_px, reason)

    return ("exit", pnl, close_px, reason)


def process_fade_hybrid_tick(
    state: FadeHybridState,
    *,
    entry_price: float,
    price: float,
    momentum: Optional[float],
    ts: float,
    rich_ticks: Sequence[Mapping[str, Any]],
    cfg: Any,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Hybrid watch tick: breakdown > 2nd fade > range-hold continue > other structural."""
    state.ticks_in_watch += 1
    state.fade_watch_hold_sec = max(0.0, ts - state.entry_ts)
    pnl = _pnl(entry_price, price)
    peak_pnl = max(state.peak_pnl, pnl)
    state.peak_pnl = peak_pnl

    if price > state.peak_price:
        state.peak_price = price
    if price < state.post_low:
        state.post_low = price

    sig = combined_exit_signal_on_latest_tick(
        rich_ticks, entry_price, _cfg_for_v1_signal(cfg)
    )
    if sig is not None:
        sig_pnl, sig_reason, _ = sig
        if sig_reason not in FADE_WATCH_TRIGGER_REASONS:
            state.last_signals = {
                "fade_watch_exit": True,
                "fade_watch_exit_reason": f"fade_hybrid_structural_exit",
                "exit_pnl": sig_pnl,
            }
            return f"fade_hybrid_structural_exit", fade_hybrid_log_fields(state)

    if breakdown_confirmed_hybrid(
        momentum=momentum,
        pnl=pnl,
        take_reached_at_fade=state.take_reached_at_fade,
        price=price,
        state=state,
    ):
        state.breakdown_confirmed_exit = True
        state.last_signals = {
            "fade_watch_exit": True,
            "fade_watch_exit_reason": "fade_hybrid_breakdown",
            "exit_pnl": pnl,
        }
        return "fade_hybrid_breakdown", fade_hybrid_log_fields(state)

    if sig is not None and sig[1] in FADE_WATCH_TRIGGER_REASONS:
        state.fade_signal_count += 1
        if state.fade_signal_count >= 2:
            if not range_hold_protect_hybrid(
                pnl=pnl,
                peak_pnl=peak_pnl,
                price=price,
                fade_price=state.fade_price,
            ):
                state.second_fade_exit = True
                state.last_signals = {
                    "fade_watch_exit": True,
                    "fade_watch_exit_reason": "fade_hybrid_second_fade",
                    "exit_pnl": float(sig[0]),
                }
                return "fade_hybrid_second_fade", fade_hybrid_log_fields(state)

    if range_hold_protect_hybrid(
        pnl=pnl,
        peak_pnl=peak_pnl,
        price=price,
        fade_price=state.fade_price,
    ):
        state.range_hold_protect_count += 1
        state.last_signals = {"fade_watch_exit": False}
        return None

    state.last_signals = {"fade_watch_exit": False}
    return None


def enter_fade_shadow_state(
    *,
    policy: str,
    entry_time: str,
    entry_ts: float,
    initial_reason: str,
    fade_price: float,
    fade_momentum: Optional[float],
    mfe_at_fade: float,
    entry_price: float,
    take_reached: bool,
) -> FadeWatchState:
    if uses_fade_hybrid_shadow(policy):
        return FadeHybridState.enter_hybrid(
            entry_time=entry_time,
            entry_ts=entry_ts,
            initial_reason=initial_reason,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            mfe_at_fade=mfe_at_fade,
            entry_price=entry_price,
            take_reached=take_reached,
        )
    if uses_fade_breakdown_shadow(policy):
        return FadeBreakdownState.enter_breakdown(
            entry_time=entry_time,
            entry_ts=entry_ts,
            initial_reason=initial_reason,
            fade_price=fade_price,
            fade_momentum=fade_momentum,
            mfe_at_fade=mfe_at_fade,
            entry_price=entry_price,
            take_reached=take_reached,
        )
    return FadeWatchState.enter(
        entry_time=entry_time,
        entry_ts=entry_ts,
        initial_reason=initial_reason,
        fade_price=fade_price,
        fade_momentum=fade_momentum,
        mfe_at_fade=mfe_at_fade,
        entry_price=entry_price,
    )


def process_fade_shadow_watch_tick(
    state: FadeWatchState,
    *,
    entry_price: float,
    price: float,
    momentum: Optional[float],
    ts: float,
    rich_ticks: Sequence[Mapping[str, Any]],
    cfg: Any,
    policy: str,
) -> Optional[tuple[str, dict[str, Any]]]:
    if uses_fade_hybrid_shadow(policy) and isinstance(state, FadeHybridState):
        return process_fade_hybrid_tick(
            state,
            entry_price=entry_price,
            price=price,
            momentum=momentum,
            ts=ts,
            rich_ticks=rich_ticks,
            cfg=cfg,
        )
    if uses_fade_breakdown_shadow(policy) and isinstance(state, FadeBreakdownState):
        return process_fade_breakdown_tick(
            state,
            entry_price=entry_price,
            price=price,
            momentum=momentum,
            ts=ts,
            rich_ticks=rich_ticks,
            cfg=cfg,
        )
    from research.fade_watch_shadow import process_fade_watch_tick

    return process_fade_watch_tick(
        state,
        entry_price=entry_price,
        price=price,
        momentum=momentum,
        ts=ts,
    )


def shadow_watch_log_fields(state: FadeWatchState, policy: str) -> dict[str, Any]:
    if uses_fade_hybrid_shadow(policy) and isinstance(state, FadeHybridState):
        return fade_hybrid_log_fields(state)
    if uses_fade_breakdown_shadow(policy) and isinstance(state, FadeBreakdownState):
        return fade_breakdown_log_fields(state)
    return fade_watch_log_fields(state)

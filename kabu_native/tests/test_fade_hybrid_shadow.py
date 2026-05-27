"""Unit tests for Phase162 G hybrid fade shadow state machine."""

from __future__ import annotations

from research.fade_hybrid_shadow import (
    FadeHybridState,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
    breakdown_confirmed_hybrid,
    combined_exit_or_fade_shadow_trigger,
    enter_fade_shadow_state,
    process_fade_hybrid_tick,
    range_hold_protect_hybrid,
    uses_fade_hybrid_shadow,
)
from research.fade_watch_shadow import FADE_WATCH_TRIGGER_REASONS


class _Cfg:
    structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW
    favorable_fade_ratio = 0.85
    momentum_weaken_ratio = 0.85
    price_momentum_fade_ratio = 0.85
    hard_stop_pct = 1.2
    hold_quality_delta = 0.03


def test_uses_fade_hybrid_shadow():
    assert uses_fade_hybrid_shadow(POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW)


def test_first_fade_enters_watch_not_exit():
    entry = 100.0
    ticks = [
        {"price": 101.0, "pnl_pct": 1.0, "momentum": 0.5, "ts_epoch": 1.0},
        {
            "price": 100.5,
            "pnl_pct": 0.5,
            "momentum": 0.1,
            "ts_epoch": 2.0,
            "quality": 0.5,
            "favorable": 0.4,
        },
    ]
    trig = combined_exit_or_fade_shadow_trigger(ticks, entry, _Cfg(), take_reached=False)
    assert trig is not None
    assert trig[0] == "fade_watch"


def test_breakdown_confirmed_exits_from_watch():
    state = enter_fade_shadow_state(
        policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
        entry_time="2026-05-21T10:00:00+09:00",
        entry_ts=100.0,
        initial_reason="momentum_fade_exit",
        fade_price=100.5,
        fade_momentum=0.2,
        mfe_at_fade=0.5,
        entry_price=100.0,
        take_reached=False,
    )
    assert isinstance(state, FadeHybridState)
    assert breakdown_confirmed_hybrid(
        momentum=0.05,
        pnl=-1.0,
        take_reached_at_fade=False,
        price=99.0,
        state=state,
    )


def test_range_hold_protect_blocks_second_fade_exit():
    assert range_hold_protect_hybrid(pnl=0.2, peak_pnl=0.5, price=100.5, fade_price=100.5)
    state = FadeHybridState.enter_hybrid(
        entry_time="t",
        entry_ts=1.0,
        initial_reason="momentum_fade_exit",
        fade_price=100.5,
        fade_momentum=0.2,
        mfe_at_fade=0.5,
        entry_price=100.0,
        take_reached=False,
    )
    state.fade_signal_count = 1
    assert not breakdown_confirmed_hybrid(
        momentum=0.2,
        pnl=0.1,
        take_reached_at_fade=False,
        price=100.5,
        state=state,
    )

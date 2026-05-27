"""Tests for Phase 135 fade switch cooldown shadow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "kabu_native" / "src", ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.fade_switch_cooldown_shadow import (
    FadeSwitchCooldownState,
    cross_symbol_switch_blocked,
    process_fade_switch_cooldown_tick,
    uses_fade_switch_cooldown_shadow,
)


def test_release_on_new_high() -> None:
    st = FadeSwitchCooldownState.enter(
        symbol="7203.T",
        fade_exit_time="2026-05-21T10:00:00+09:00",
        fade_exit_ts=0.0,
        fade_exit_reason="momentum_fade_exit",
        entry_price=1000.0,
        fade_price=1005.0,
        fade_momentum=0.3,
        fade_pnl=0.5,
    )
    assert process_fade_switch_cooldown_tick(st, price=1004.0, momentum=0.28, ts=1.0) is None
    reason = process_fade_switch_cooldown_tick(st, price=1010.0, momentum=0.35, ts=2.0)
    assert reason == "new_high_after_fade"
    assert st.released


def test_cross_symbol_block_until_release() -> None:
    st = FadeSwitchCooldownState.enter(
        symbol="7203.T",
        fade_exit_time="t",
        fade_exit_ts=0.0,
        fade_exit_reason="price_momentum_fade_exit",
        entry_price=100.0,
        fade_price=101.0,
        fade_momentum=0.2,
        fade_pnl=1.0,
    )
    blocked, sym = cross_symbol_switch_blocked({"7203.T": st}, new_symbol="9984.T")
    assert blocked and sym == "7203.T"
    st.released = True
    blocked2, _ = cross_symbol_switch_blocked({"7203.T": st}, new_symbol="9984.T")
    assert not blocked2


def test_policy_constant() -> None:
    assert uses_fade_switch_cooldown_shadow("fade_switch_cooldown_shadow")

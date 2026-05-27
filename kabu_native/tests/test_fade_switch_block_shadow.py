"""Tests for Phase 141 fade switch block shadow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "kabu_native" / "src", ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.fade_switch_block_shadow import (
    BLOCK_REASON,
    FadeSwitchBlockState,
    cross_symbol_fade_switch_blocked,
    uses_fade_switch_block_shadow,
)


def test_cross_symbol_blocked_no_release() -> None:
    st = FadeSwitchBlockState.enter(
        old_symbol="7203.T",
        fade_exit_time="2026-05-21T10:00:00+09:00",
        fade_exit_reason="momentum_fade_exit",
    )
    blocked, sym, reason = cross_symbol_fade_switch_blocked(
        {"7203.T": st}, new_symbol="9984.T"
    )
    assert blocked
    assert sym == "7203.T"
    assert reason == "momentum_fade_exit"


def test_same_symbol_not_blocked() -> None:
    st = FadeSwitchBlockState.enter(
        old_symbol="7203.T",
        fade_exit_time="t",
        fade_exit_reason="price_momentum_fade_exit",
    )
    blocked, _, _ = cross_symbol_fade_switch_blocked({"7203.T": st}, new_symbol="7203.T")
    assert not blocked


def test_policy_name() -> None:
    assert uses_fade_switch_block_shadow(
        "combined_structural_exit_v1_fade_switch_block_shadow"
    )
    assert BLOCK_REASON == "fade_cross_symbol_switch_block"

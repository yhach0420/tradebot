"""Tests for Phase 143 fade first switch block shadow."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "kabu_native" / "src", ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.fade_first_switch_block_shadow import (
    BLOCK_REASON,
    FadeFirstSwitchBlockState,
    try_block_first_cross_symbol,
    uses_fade_first_switch_block_shadow,
)


def test_blocks_only_first_cross() -> None:
    st = FadeFirstSwitchBlockState.enter(
        old_symbol="7203.T",
        fade_exit_time="2026-05-21T10:00:00+09:00",
        fade_exit_ts=100.0,
        fade_exit_reason="momentum_fade_exit",
    )
    states = {"7203.T|t": st}
    blocked, _ = try_block_first_cross_symbol(
        states,
        new_symbol="9984.T",
        new_entry_time="2026-05-21T10:05:00+09:00",
        new_entry_ts=200.0,
    )
    assert blocked
    blocked2, _ = try_block_first_cross_symbol(
        states,
        new_symbol="6758.T",
        new_entry_time="2026-05-21T10:10:00+09:00",
        new_entry_ts=250.0,
    )
    assert not blocked2


def test_same_symbol_not_in_try_block() -> None:
    st = FadeFirstSwitchBlockState.enter(
        old_symbol="7203.T",
        fade_exit_time="t",
        fade_exit_ts=0.0,
        fade_exit_reason="momentum_fade_exit",
    )
    blocked, _ = try_block_first_cross_symbol(
        states={"k": st}, new_symbol="7203.T", new_entry_time="t2", new_entry_ts=1.0
    )
    assert not blocked


def test_policy_name() -> None:
    assert uses_fade_first_switch_block_shadow(
        "combined_structural_exit_v1_fade_first_switch_block_shadow"
    )
    assert BLOCK_REASON == "fade_first_cross_symbol_switch"

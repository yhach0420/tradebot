"""Tests for Phase 127 fade_watch shadow policy."""

from __future__ import annotations

from research.fade_watch_shadow import (
    FADE_WATCH_TRIGGER_REASONS,
    FadeWatchState,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
    combined_exit_or_fade_watch_trigger,
    is_fade_watch_review_reason,
    process_fade_watch_tick,
    uses_fade_watch_shadow,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
    tick_from_candidate,
)


class _Cfg:
    structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW
    take_quality_drop = 0.08
    momentum_weaken_ratio = 0.85
    favorable_fade_ratio = 0.85
    hard_stop_pct = 1.2
    price_momentum_fade_ratio = 0.85


class _CfgV1:
    structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    take_quality_drop = 0.08
    momentum_weaken_ratio = 0.85
    favorable_fade_ratio = 0.85
    hard_stop_pct = 1.2
    price_momentum_fade_ratio = 0.85


def _ticks(prices: list[float], moms: list[float]) -> list[dict]:
    out = []
    for i, (px, mom) in enumerate(zip(prices, moms)):
        out.append(
            {
                "ts": f"t{i}",
                "ts_epoch": float(i),
                "price": px,
                "pnl_pct": round((px - 100.0) / 100.0 * 100.0, 4),
                "quality": 0.8,
                "momentum": mom,
                "favorable": 0.9,
                "pure_price_momentum": 0.01,
            }
        )
    return out


def test_uses_fade_watch_shadow_policy_name() -> None:
    assert uses_fade_watch_shadow(POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW)
    assert not uses_fade_watch_shadow(POLICY_COMBINED_STRUCTURAL_EXIT_V1)


def test_fade_trigger_enters_watch_not_immediate_exit() -> None:
    ticks = _ticks([100.0, 101.0, 100.5], [0.6, 0.7, 0.4])
    ticks[-1]["momentum"] = 0.4
    ticks[-2]["momentum"] = 0.7
    trigger = combined_exit_or_fade_watch_trigger(ticks, 100.0, _Cfg())
    assert trigger is not None
    kind, _pnl, _px, reason = trigger
    assert kind == "fade_watch"
    assert reason in FADE_WATCH_TRIGGER_REASONS


def test_v1_policy_still_exits_on_fade() -> None:
    ticks = _ticks([100.0, 101.0, 100.5], [0.6, 0.7, 0.4])
    sig = combined_exit_signal_on_latest_tick(ticks, 100.0, _CfgV1())
    assert sig is not None
    trigger = combined_exit_or_fade_watch_trigger(ticks, 100.0, _CfgV1())
    assert trigger is not None
    assert trigger[0] == "exit"


def test_fade_watch_exits_on_giveback_not_fixed_time() -> None:
    state = FadeWatchState.enter(
        entry_time="t1",
        entry_ts=1.0,
        initial_reason="momentum_fade_exit",
        fade_price=100.5,
        fade_momentum=0.5,
        mfe_at_fade=0.5,
        entry_price=100.0,
    )
    # peak then giveback >25%
    process_fade_watch_tick(
        state, entry_price=100.0, price=101.0, momentum=0.55, ts=2.0
    )
    result = process_fade_watch_tick(
        state, entry_price=100.0, price=100.6, momentum=0.45, ts=3.0
    )
    assert result is not None
    reason, _log = result
    assert reason == "fade_watch_giveback"
    assert is_fade_watch_review_reason(reason)


def test_review_reasons_recognized() -> None:
    for reason in (
        "fade_watch_giveback",
        "fade_watch_breakdown",
        "fade_watch_momentum_fade",
        "fade_watch_session_close",
    ):
        assert is_fade_watch_review_reason(reason)

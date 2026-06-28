"""Tests for Phase590 volume gate relaxation shadow."""

from __future__ import annotations

from small_paper.volume_gate_relaxation_shadow import (
    RELAXATION_V80,
    RELAXATION_V90,
    VolumeGateRelaxationShadowState,
    compute_volume_shadow_eval,
    record_volume_gate_shadow_eval,
    shadow_enabled,
    volume_shadow_summary_fields,
)


def test_shadow_enabled_requires_daytrade():
    class C:
        daytrade_suitability_enabled = False
        volume_gate_relaxation_shadow_enabled = True

    assert shadow_enabled(C()) is False


def test_compute_shadow_eval_pass_and_rescue():
    trade = {"volatility_liquidity_score": 95.0, "trading_value": 1e9}
    row = compute_volume_shadow_eval(
        trade=trade,
        threshold_v100=100.0,
        symbol="7203",
        timestamp="2026-06-18T09:10:00+09:00",
        current_reject_reason="daytrade_suitability",
    )
    assert row is not None
    assert row["pass_v100"] is False
    assert row["shadow_rescue_v90"] is True
    assert row["shadow_rescue_v80"] is True


def test_record_increments_rescue_counters():
    state = VolumeGateRelaxationShadowState()
    trade = {"volatility_liquidity_score": 95.0, "trading_value": 1e9}
    row = record_volume_gate_shadow_eval(
        state,
        trade=trade,
        threshold=100.0,
        symbol="7203",
        timestamp="t1",
        reject_reason="daytrade_suitability",
    )
    assert row is not None
    assert state.eval_count == 1
    assert state.rescue_v90_count == 1
    assert state.rescue_v80_count == 1


def test_summary_fields_include_monitor_keys():
    state = VolumeGateRelaxationShadowState()
    state.eval_count = 10
    state.rescue_v90_count = 3
    out = volume_shadow_summary_fields(state, replay_v90_pnl=1000.0, baseline_pnl=800.0)
    assert out["volume_shadow_v90_rescued_count"] == 3
    assert out["volume_shadow_v90_delta"] == 200.0
    assert "volume_shadow_monitor_status" in out

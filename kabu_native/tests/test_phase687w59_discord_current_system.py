"""Phase687W59 — Discord current-system update tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from small_paper.discord_current_system_summary import (
    build_runtime_status,
    build_shadow_summary_structured,
    extract_exit_forward_tags,
    render_entry_aborted_lines,
    render_paper_start_lines,
    split_discord_messages,
)
from small_paper.entry_execution_integrity import is_official_entry_ready
from small_paper.flat_weak_range_forward_shadow import FlatWeakRangeForwardShadowCounters
from small_paper.forward_observer_defaults import (
    COST_AWARE_ENV,
    PAPER_RUNTIME_ENV,
    PULLBACK_VOLUME_ENV,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in (COST_AWARE_ENV, PULLBACK_VOLUME_ENV, PAPER_RUNTIME_ENV):
        monkeypatch.delenv(k, raising=False)


def test_startup_shows_mainline_and_observers(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    cfg = SimpleNamespace(
        pbv2_flat_band_mainline_enabled=True,
        entry_price_risk_guard_enabled=True,
        classic_late_chase_rsi_guard_enabled=True,
        flat_weak_range_shadow_enabled=True,
        max_concurrent_positions=5,
        hard_stop_pct=1.2,
    )
    status = build_runtime_status(cfg, trading_date="2026-07-18")
    lines = "\n".join(render_paper_start_lines(status))
    assert "[TRADEBOT PAPER START]" in lines
    assert "flat_band_mainline: ON" in lines
    assert "Cost-Aware Entry: ON" in lines
    assert "Pullback Volume: ON" in lines
    assert "Flat Weak + Range: ON" in lines
    assert "observe-only" in lines


def test_explicit_zero_off_in_startup(monkeypatch):
    monkeypatch.setenv(PAPER_RUNTIME_ENV, "1")
    monkeypatch.setenv(PULLBACK_VOLUME_ENV, "0")
    monkeypatch.setenv(COST_AWARE_ENV, "0")
    status = build_runtime_status(SimpleNamespace(flat_weak_range_shadow_enabled=False))
    text = "\n".join(render_paper_start_lines(status))
    assert "Pullback Volume: OFF (explicit)" in text
    assert "Cost-Aware Entry: OFF (explicit)" in text


def test_official_entry_gate():
    ready = {
        "official_entry": True,
        "position_registered": True,
        "accept_stage": "official_entry",
        "position_id": "pid-1",
        "entry_price": 4430.0,
    }
    assert is_official_entry_ready(ready) is True
    assert is_official_entry_ready({"official_entry": False, "gate_accepted": True}) is False
    assert is_official_entry_ready(
        {"position_registered": True, "position_id": "x", "entry_price": None}
    ) is False


def test_entry_aborted_lines_once_format():
    lines = render_entry_aborted_lines(
        {"symbol": "6327.T"},
        reason="invalid_entry_payload",
        stage="execution_payload_validation",
    )
    text = "\n".join(lines)
    assert "[ENTRY ABORTED]" in text
    assert "official entry: NOT CREATED" in text
    assert "6327.T" in text


def test_exit_forward_tags_only_when_present():
    tags = extract_exit_forward_tags(
        {
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
            "pullback_misread_guard_shadow_blocked": True,
            "pullback_volume_bucket": "low",
        }
    )
    assert "flat_weak_range: block" in tags
    assert "pullback_misread: hit" in tags
    assert "pullback_volume: low" in tags
    assert extract_exit_forward_tags({}) == []
    assert extract_exit_forward_tags({"pullback_volume_bucket": "missing"}) == []


def test_fwr_join_recovers_missing_exit_fields():
    c = FlatWeakRangeForwardShadowCounters()
    c.record_accept(
        {
            "symbol": "1234.T",
            "entry_time": "2026-07-17T09:10:00+09:00",
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
            "minutes_from_open": 10,
        }
    )
    c.bind_position(
        position_id="pid-1",
        symbol="1234.T",
        entry_time="2026-07-17T09:10:00+09:00",
    )
    # EXIT row missing FWR flags (7/17-style bug)
    c.record_exit(
        {
            "position_id": "pid-1",
            "symbol": "1234.T",
            "entry_time": "2026-07-17T09:10:00+09:00",
            "entry_price": 1000.0,
            "exit_price": 980.0,
            "exit_reason": "stop_hit",
            "stop_hit": True,
        }
    )
    s = c.summary_fields()
    assert s["flat_weak_range_shadow_completed"] == 1
    assert s["flat_weak_range_shadow_blocked_losers"] == 1
    assert s["flat_weak_range_shadow_actual_total_pnl_yen_100"] != 0.0
    assert s["flat_weak_range_shadow_delta_yen"] != 0.0


def test_shadow_summary_sections_ordered():
    summary = {
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 8,
        "flat_weak_range_shadow_block_count": 3,
        "flat_weak_range_shadow_kept_count": 5,
        "flat_weak_range_shadow_blocked_winners": 1,
        "flat_weak_range_shadow_blocked_losers": 2,
        "flat_weak_range_shadow_actual_total_pnl_yen_100": -9000,
        "flat_weak_range_shadow_total_pnl_yen_100": 1000,
        "flat_weak_range_shadow_delta_yen": 10000,
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_blocked_count": 4,
        "pullback_misread_guard_shadow_delta_yen": 2000,
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 2,
            "candidates": 10,
            "official_entry_match": 1,
            "official_entry_mismatch": 1,
            "n_closed": 1,
        },
        "pullback_volume_forward": {
            "enabled": True,
            "hits": 4,
            "volume_high_n": 1,
            "volume_low_n": 2,
            "volume_mid_n": 1,
            "volume_high": {"n": 1, "healthy_rate": 1.0, "collapse_rate": 0.0},
            "volume_low": {"n": 2, "healthy_rate": 0.0, "collapse_rate": 1.0},
            "board_volume": {"board_down_vol_low": {"n": 1}},
        },
        "official_entry_count": 10,
    }
    out = build_shadow_summary_structured(summary, am_pm="am")
    text = out["discord_text"]
    assert text.startswith("[SHADOW SUMMARY - AM]")
    assert text.index("--- Observer Status ---") < text.index("--- Cost-Aware ENTRY ---")
    assert text.index("--- Cost-Aware ENTRY ---") < text.index("--- Flat Weak + Range ---")
    assert text.index("--- Flat Weak + Range ---") < text.index("--- PullbackMisread ---")
    assert text.index("--- PullbackMisread ---") < text.index("--- Pullback Volume Forward ---")
    assert "--- Data Completeness ---" in text
    assert "採用" not in text


def test_shadow_not_mixed_into_actual_keys():
    # render modules keep shadow under separate section titles
    out = build_shadow_summary_structured(
        {"flat_weak_range_shadow_enabled": True, "flat_weak_range_shadow_target_count": 1},
        am_pm="pm",
    )
    assert "[PAPER SUMMARY" not in out["discord_text"]


def test_discord_render_fail_open_and_length():
    big = build_shadow_summary_structured(
        {
            "flat_weak_range_shadow_enabled": True,
            "flat_weak_range_shadow_target_count": 1,
            "cost_aware_entry_shadow": {"enabled": True, "selection_cycles": 1, "candidates": 1},
            "pullback_volume_forward": {"enabled": True, "hits": 1, "volume_high_n": 0, "volume_low_n": 1},
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 1,
        },
        am_pm="am",
    )
    parts = split_discord_messages(out["discord_text"] if False else big["discord_text"])
    assert 1 <= len(parts) <= 2
    assert all(len(p) <= 4000 for p in parts)


def test_pullback_volume_eligible_recorded_mismatch_incomplete():
    # W63: Misread hits must not be PV denominator; use eligible/recorded.
    out = build_shadow_summary_structured(
        {
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_blocked_count": 5,
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 2,
                "hits": 2,
            },
            "official_entry_count": 10,
        },
        am_pm="am",
    )
    text = out["discord_text"]
    assert "PullbackMisread hits:" in text
    assert "Pullback Volume eligible:" in text
    assert "Pullback Volume recorded:" in text
    assert "5 / 2" not in text  # invalid misread-as-denominator form
    assert "2 / 5" in text
    assert "status: INCOMPLETE" in text

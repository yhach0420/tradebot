"""Phase687W25A — Discord content accuracy (display-only)."""

from __future__ import annotations

import inspect

from notify.discord_notification_formatter import (
    _communication_impact_defaults,
    format_communication_degraded,
)
from small_paper.board_dynamic_trailing_shadow import (
    BOARD_HIGH_ACTIVATE_PCT,
    BOARD_HIGH_GIVEBACK_FRAC,
    BOARD_LOW_ACTIVATE_PCT,
    BOARD_LOW_GIVEBACK_FRAC,
    trailing_params_for_board_tier,
)
from small_paper.discord_message_builder import (
    build_exit_detail,
    format_board_dynamic_trailing_lines,
)


def test_discord_webhook_failure_entry_eval_continues():
    text = format_communication_degraded(
        {"target": "Discord webhook", "status": "SEND_FAILED", "last_push_age_sec": "N/A"}
    )
    assert "ENTRY評価: 継続" in text
    assert "Paper本体への影響: NONE" in text
    assert "一時停止" not in text


def test_kabu_push_failure_entry_eval_paused():
    text = format_communication_degraded(
        {
            "target": "Kabu PUSH",
            "status": "DEGRADED_NO_PUSH",
            "last_push_age_sec": 45.0,
            "reconnect": "1回目",
        }
    )
    assert "ENTRY評価: 一時停止" in text
    assert "対象: Kabu PUSH" in text


def test_capture_fanout_failure_entry_eval_continues():
    text = format_communication_degraded(
        {"target": "Capture fan-out", "status": "INGEST_DOWN"}
    )
    assert "ENTRY評価: 継続" in text
    assert "Paper本体への影響: NONE" in text


def test_board_high_runtime_values_displayed():
    act, gb, tier = trailing_params_for_board_tier(80.0)  # high side of split
    assert tier == "board_high"
    assert act == BOARD_HIGH_ACTIVATE_PCT
    assert gb == BOARD_HIGH_GIVEBACK_FRAC
    detail = build_exit_detail(
        symbol="4174.T",
        entry_price=1000.0,
        exit_price=1010.0,
        pnl_pct=1.0,
        mfe_pct=1.5,
        mae_pct=-0.1,
        hold_minutes=10.0,
        exit_reason="trailing_mfe_exit",
        pnl_yen_100=1000.0,
        board_dynamic_trailing_tier=tier,
        board_dynamic_trailing_activate_pct=act,
        board_dynamic_trailing_giveback_frac=gb,
        exit_time="2026-07-14T10:00:00+09:00",
    )
    assert f"board tier: {tier}" in detail
    assert f"activation threshold: {act:.2f}%" in detail
    assert f"giveback threshold: {int(round(gb * 100))}%" in detail
    assert "mid (activate 0.60%" not in detail
    assert "giveback 35%" not in detail


def test_board_low_runtime_values_displayed():
    act, gb, tier = trailing_params_for_board_tier(10.0)  # low side
    assert tier == "board_low"
    assert act == BOARD_LOW_ACTIVATE_PCT
    assert gb == BOARD_LOW_GIVEBACK_FRAC
    detail = build_exit_detail(
        symbol="7203.T",
        entry_price=2800.0,
        exit_price=2810.0,
        pnl_pct=0.36,
        mfe_pct=0.8,
        mae_pct=-0.2,
        hold_minutes=8.0,
        exit_reason="trailing_mfe_exit",
        board_dynamic_trailing_tier=tier,
        board_dynamic_trailing_activate_pct=act,
        board_dynamic_trailing_giveback_frac=gb,
    )
    assert f"board tier: board_low" in detail
    assert f"activation threshold: {BOARD_LOW_ACTIVATE_PCT:.2f}%" in detail
    assert f"giveback threshold: {int(round(BOARD_LOW_GIVEBACK_FRAC * 100))}%" in detail


def test_formatter_has_no_hardcoded_trailing_thresholds():
    src = inspect.getsource(format_board_dynamic_trailing_lines)
    src2 = inspect.getsource(build_exit_detail)
    for forbidden in ("0.60", "0.35", '"mid"', "'mid'", "BOARD_HIGH", "BOARD_LOW"):
        assert forbidden not in src
        # build_exit_detail must not invent thresholds either
        if forbidden in ('"mid"', "'mid'", "0.35"):
            assert forbidden not in src2


def test_runtime_mismatch_would_fail_display_contract():
    """Display must echo the event fields; inventing mid/0.60/35 is a contract fail."""
    # Simulate wrong legacy sample — display still echoes inputs (no rewrite)
    detail = build_exit_detail(
        symbol="X",
        entry_price=1.0,
        exit_price=1.0,
        pnl_pct=0.0,
        mfe_pct=0.0,
        mae_pct=0.0,
        hold_minutes=1.0,
        exit_reason="trailing_mfe_exit",
        board_dynamic_trailing_tier="board_high",
        board_dynamic_trailing_activate_pct=BOARD_HIGH_ACTIVATE_PCT,
        board_dynamic_trailing_giveback_frac=BOARD_HIGH_GIVEBACK_FRAC,
    )
    # Must match runtime SoT, not legacy mid/0.60/35
    assert "board tier: board_high" in detail
    assert "activation threshold: 1.00%" in detail
    assert "giveback threshold: 60%" in detail
    assert "board tier: mid" not in detail


def test_notification_behavior_unchanged_markers():
    # Content-only patch: defaults still distinguish targets without new send paths
    assert _communication_impact_defaults("Discord webhook") == ("継続", "NONE")
    assert _communication_impact_defaults("Kabu PUSH") == ("一時停止", "DEGRADED")
    assert _communication_impact_defaults("Capture fan-out") == ("継続", "NONE")


def test_submit_cancel_zero_in_comm_text():
    text = format_communication_degraded({"target": "Discord webhook"})
    assert "実注文: DISABLED" in text

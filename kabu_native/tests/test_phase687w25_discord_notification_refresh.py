"""Phase687W25 — Discord notification content refresh tests."""

from __future__ import annotations

import json
from pathlib import Path

from notify.discord_notification_formatter import (
    format_capture_status_body,
    format_communication_degraded,
    format_communication_recovered,
    format_shadow_summary,
)
from small_paper.discord_message_builder import (
    DEFAULT_POSITION_CAP,
    PAPER_ONLY_FOOTER,
    build_entry_cap_blocked_detail,
    build_entry_detail,
    build_exit_detail,
    build_universe_refresh_overview,
    format_discord_summary_lines,
    humanize_exit_reason,
)


def test_entry_formatter_layout():
    detail = build_entry_detail(
        symbol="4174.T",
        entry_price=925.0,
        stop_price=900.0,
        slot_usage="3/5",
        entry_score_v2=3,
        data={
            "entry_type": "PBV2",
            "momentum_continuation_score": 0.2,
            "board_imbalance_tertile": "mid",
            "price_age_sec": 0.8,
            "board_age_sec": 0.2,
            "price_freshness_source": "event_fresh",
            "entry_expectancy_score_v2": 3,
        },
        entry_time="2026-07-14T10:06:53+09:00",
    )
    assert "4174.T" in detail
    assert "10:06:53" in detail
    assert "価格: 925円" in detail
    assert "方式: PBv2" in detail
    assert "score_v2: 3" in detail
    assert "保有: 3/5" in detail
    assert "price_age_sec: 0.8" in detail
    assert "board_age_sec: 0.2" in detail
    assert "price_source: event_fresh" in detail
    assert PAPER_ONLY_FOOTER in detail
    assert "cap3" not in detail.lower()
    assert "CAPTURE_ONLINE" not in detail
    assert "session_id:" not in detail


def test_or_entry_shows_or_reason():
    detail = build_entry_detail(
        symbol="7203.T",
        entry_price=2800.0,
        stop_price=2700.0,
        slot_usage="1/5",
        entry_score_v2=2,
        data={"entry_type": "OR_OVERLAY", "or_reason": "opening_range_break"},
        entry_time="2026-07-14T09:05:00+09:00",
    )
    assert "方式: OR" in detail
    assert "OR理由: opening_range_break" in detail


def test_exit_reason_labels_and_stale_tag():
    assert humanize_exit_reason("stop_hit") == "損切り"
    assert humanize_exit_reason("trailing_mfe_exit") == "トレーリング決済"
    assert humanize_exit_reason("no_progress_exit") == "停滞ポジション整理"
    assert humanize_exit_reason("morning_session_close") == "前場終了前の決済"
    detail = build_exit_detail(
        symbol="4174.T",
        entry_price=925.0,
        exit_price=925.0,
        pnl_pct=0.0,
        mfe_pct=0.0,
        mae_pct=0.0,
        hold_minutes=15.116,
        exit_reason="no_progress_exit",
        pnl_yen_100=0.0,
        exit_time="2026-07-14T10:21:59+09:00",
        market_time_age_sec=2070.0,
        stale_trade=True,
        price_freshness_source="liquidity_stale_trade",
    )
    assert "理由: 停滞ポジション整理" in detail
    assert "保有時間: 15分07秒" in detail
    assert "stale_trade: true" in detail
    assert "reject" not in detail.lower() or "rejectではない" in detail
    assert "liquidity_stale_trade" in detail
    assert PAPER_ONLY_FOOTER in detail


def test_cap_blocked_cap5_and_no_cap3():
    detail = build_entry_cap_blocked_detail(
        symbol="6981.T",
        entry_score_v2=3,
        data={"entry_type": "PBV2", "price_age_sec": 0.5, "board_age_sec": 0.1, "event_time": "2026-07-14T10:00:00+09:00"},
        active_positions=5,
        position_cap=5,
    )
    assert "position_cap: 5" in detail
    assert "方式: PBv2" in detail
    assert "保有上限到達" in detail
    assert "cap3" not in detail.lower()
    assert "cap=3" not in detail.lower()
    assert DEFAULT_POSITION_CAP == 5


def test_refresh_topology():
    overview = build_universe_refresh_overview(
        session_label="AM",
        refresh_time="10:00",
        added=["1234"],
        removed=[],
        watch_symbol_count=50,
        status="completed",
        core10_count=10,
        dynamic40_count=40,
        registered_count=50,
    )
    assert "結果: SUCCESS" in overview
    assert "登録: 50 / 50" in overview
    assert "Core10: 10" in overview
    assert "Dynamic40: 40" in overview
    assert "SINGLE_INGRESS_LOCAL_FANOUT" in overview
    assert "PASSIVE_DUAL" not in overview
    assert "実注文: DISABLED" in overview


def test_summary_canonical_no_avg_pnl_pct_cap5():
    lines = format_discord_summary_lines(
        {
            "trade_count": 4,
            "win_count": 2,
            "loss_count": 1,
            "flat_count": 1,
            "win_rate_yen_100": 0.5,
            "total_pnl_yen_100": 1000,
            "avg_pnl_yen_100": 250,
            "avg_pnl_pct": 99.9,
            "total_pnl_pct": 12.3,
            "profit_factor_yen_100": "inf",
            "gross_profit_yen_100": 2000,
            "gross_loss_yen_100": 0,
            "stop_count": 1,
            "stop_rate": 0.25,
            "best_trade": "—",
            "worst_trade": "—",
            "max_concurrent": 3,
            "max_concurrent_cap": 5,
            "watch_symbols_count": 50,
            "traded_symbols_count": 4,
        }
    )
    text = "\n".join(lines)
    assert "取引数: 4" in text
    assert "最大同時保有 / CAP: 3 / 5" in text
    assert "PF: ∞" in text
    assert "avg_pnl_pct" not in text
    assert "total_pnl_pct" not in text
    assert PAPER_ONLY_FOOTER in text


def test_shadow_observation_separated():
    text = format_shadow_summary(
        {
            "shadow_name": "np_logger",
            "candidates": 10,
            "hypothetical_fills": 0,
            "outcome_mapping_unavailable": True,
            "blocks": 3,
            "forward_sessions": 2,
        }
    )
    assert "[SHADOW OBSERVATION]" in text
    assert "observation only" in text
    assert "outcome mapping unavailable" in text
    assert "0円改善" not in text


def test_capture_no_online_and_states():
    ready = format_capture_status_body({"status": "CAPTURE_READY_FOR_FANOUT", "written": 0})
    assert "READY_FOR_FANOUT" in ready
    assert "CAPTURE_ONLINE" not in ready
    writing = format_capture_status_body(
        {"status": "CAPTURE_WRITING", "received": 100, "written": 100, "bytes": 5000, "drops": 0, "malformed": 0}
    )
    assert "状態: WRITING" in writing
    assert "CAPTURE_ONLINE" not in writing
    # legacy online remapped
    legacy = format_capture_status_body({"status": "CAPTURE_ONLINE", "written": 0, "ingress": "paper_fanout"})
    assert "READY_FOR_FANOUT" in legacy
    assert "CAPTURE_ONLINE" not in legacy
    stale = format_capture_status_body({"status": "CAPTURE_STALE", "stale_age_sec": 130, "paper_status": "RUNNING"})
    assert "STALE" in stale
    failed = format_capture_status_body({"status": "CAPTURE_FAILED", "reason": "boom"})
    assert "FAILED" in failed
    assert "fan-out: fail-open" in failed


def test_communication_formatters_exist_but_are_preview_only():
    d = format_communication_degraded(
        {"target": "Kabu PUSH", "status": "DEGRADED_NO_PUSH", "last_push_age_sec": 45, "reconnect": "1回目"}
    )
    assert "[COMMUNICATION DEGRADED]" in d
    assert "対象: Kabu PUSH" in d
    assert "ENTRY評価: 一時停止" in d
    discord = format_communication_degraded({"target": "Discord webhook", "status": "SEND_FAILED"})
    assert "ENTRY評価: 継続" in discord
    assert "Paper本体への影響: NONE" in discord
    r = format_communication_recovered({"target": "Kabu PUSH", "down_sec": 12, "registered": "50 / 50"})
    assert "[COMMUNICATION RECOVERED]" in r


def test_submit_cancel_footer_and_no_behavior_flags():
    # Content-only: footer always paper-only
    assert "実注文なし" in PAPER_ONLY_FOOTER

"""Phase663A2 — PM notification ordering / Discord observability regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research.phase663a2_pm_notification_ordering_audit import (
    PHASE663A2_VERDICT,
    build_discord_notification_timeline,
    build_position_count_timeline,
    run_audit,
)
from small_paper.discord_message_builder import (
    build_entry_detail,
    build_universe_screening_overview,
    format_position_slot_pair,
)
from small_paper.discord_entry_delivery import DiscordPostResult, FINAL_FAILED
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier


def test_format_position_slot_pair_shows_pre_and_post():
    assert format_position_slot_pair(3, 4, 5) == "3→4/5"
    assert format_position_slot_pair(3, 3, 5) == "3/5"
    assert format_position_slot_pair(None, 3, 5) == "3/5"


def test_build_entry_detail_includes_audit_fields():
    detail = build_entry_detail(
        symbol="5801.T",
        entry_price=3596.0,
        stop_price=3550.0,
        slot_usage="3→4/5",
        entry_score_v2=4,
        data={
            "session_id": "20260708_pm_live_session_122537",
            "position_id": "5801.T|2026-07-08T13:23:45",
        },
        entry_time="2026-07-08T13:23:45+09:00",
        sent_time="2026-07-08T13:23:46+09:00",
        sequence_id=42,
    )
    assert "13:23:45" in detail
    assert "保有: 3→4/5" in detail
    assert "PAPER ONLY" in detail
    # Phase687W25: operator body omits raw session_id / sequence_id debug lines
    assert "session_id:" not in detail
    assert "sequence_id:" not in detail
    assert "sent_time:" not in detail
    assert "event_time:" not in detail


def test_universe_screening_overview_includes_generated_and_sent():
    overview = build_universe_screening_overview(
        session_label="PM Screening",
        watch_symbol_count=50,
        generated_at="2026-07-08T12:25:03+09:00",
        sent_at="2026-07-08T13:23:10+09:00",
        sequence_id=1,
    )
    assert "generated_at: 12:25:03" in overview
    assert "sent_time: 13:23:10" in overview
    assert "sequence_id: 1" in overview


def test_notify_entry_failure_logs_discord_entry_notify_failed():
    errors: list[dict] = []

    def _logger(op: str, msg: str, extra: dict) -> None:
        errors.append({"op": op, "msg": msg, **extra})

    notifier = SmallPaperDiscordNotifier(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="p",
        entry_profile="p",
        error_logger=_logger,
    )
    notifier._resolve_trade_webhook = lambda: ("http://example.invalid", "notify")  # type: ignore[method-assign]
    notifier._trade_webhook_url = "http://example.invalid"
    notifier._trade_webhook_source = "notify"

    with patch.object(
        notifier,
        "_post_with_result",
        return_value=DiscordPostResult(final_result=FINAL_FAILED, failure_reason="mock"),
    ):
        res = notifier.notify_entry(
            event={
                "symbol": "7220.T",
                "event_time": "2026-07-08T13:08:09+09:00",
                "current_price": 3515.0,
                "position_slot_before": 3,
            },
            payload={},
            open_slots=4,
            session_bucket="afternoon",
            slot_before=3,
            notify_mono=1.0,
        )
    assert res.final_result == FINAL_FAILED
    assert errors
    assert errors[0].get("error_type") == "discord_entry_notify_failed"
    assert errors[0].get("symbol") == "7220.T"


def test_phase663a2_audit_on_live_session_fixture():
    root = Path(__file__).resolve().parents[1]
    pm_dir = root / "results" / "small_paper" / "20260708" / "live_session_122537"
    if not pm_dir.is_file() and not (pm_dir / "small_paper_events.jsonl").is_file():
        pytest.skip("20260708 PM session fixture missing")
    report = run_audit(pm_session_dir=pm_dir)
    assert report["verdict"] == PHASE663A2_VERDICT
    assert report["7220_entry_accept_time"] == "2026-07-08T13:08:09+09:00"
    assert report["7220_exit_time"] == "2026-07-08T13:23:43+09:00"
    assert report["discord_error_count_session"] == 389
    assert "F_composite" in str(report["root_cause"])


def test_discord_timeline_flags_missing_entry_delivery():
    events = [
        {
            "event_time": "2026-07-08T13:08:09+09:00",
            "event_type": "accepted",
            "symbol": "7220.T",
        },
        {
            "event_time": "2026-07-08T13:23:43+09:00",
            "event_type": "observer_exit",
            "symbol": "7220.T",
            "exit_reason": "no_progress_exit",
        },
    ]
    rows = build_discord_notification_timeline(events, [], start="2026-07-08T13:00:00", end="2026-07-08T13:30:00")
    entry_row = next(r for r in rows if r["kind"] == "ENTRY_event")
    assert entry_row["inferred_discord_delivery"] == "missing_in_event_log"


def test_position_count_timeline_5801_reentry():
    events = [
        {
            "event_time": "2026-07-08T13:23:43+09:00",
            "event_type": "observer_exit",
            "symbol": "7220.T",
        },
        {
            "event_time": "2026-07-08T13:23:45+09:00",
            "event_type": "accepted",
            "symbol": "5801.T",
            "position_slot_before": 3,
            "position_slot_after": 3,
        },
    ]
    rows = build_position_count_timeline(events)
    accept = next(r for r in rows if r["action"] == "entry_accept")
    assert accept["pre_count"] == 3
    assert accept["post_count"] == 3

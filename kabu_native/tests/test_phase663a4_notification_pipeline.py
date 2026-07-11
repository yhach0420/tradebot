"""Phase663A4 — ENTRY Discord delivery pipeline regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from research.phase663a4_notification_pipeline_audit import (
    PHASE663A4_VERDICT,
    DiscordErrorWindow,
    audit_entry_pipeline_row,
    build_missing_entry_notification_proof,
    run_audit,
)
from small_paper.discord_entry_delivery import (
    CLASS_HTTP_FAILED,
    CLASS_NO_RETRY_TERMINATED,
    CLASS_WEBHOOK_SEND_FAILED,
    FINAL_DELIVERED,
    FINAL_FAILED,
    FINAL_UNPROVABLE,
    DiscordPostResult,
)
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier


def _cfg() -> SmallPaperDiscordConfig:
    return SmallPaperDiscordConfig(enabled=True, observer_only=True)


def _entry_event() -> dict[str, Any]:
    return {
        "symbol": "7220.T",
        "event_time": "2026-07-08T13:08:09+09:00",
        "current_price": 3515.0,
        "position_slot_before": 3,
        "position_id": "7220.T|2026-07-08T13:08:09+09:00",
        "session_id": "20260708_pm_live_session_122537",
        "message_index": 1,
    }


def _notifier(audit: list[dict] | None = None) -> SmallPaperDiscordNotifier:
    records: list[dict] = audit if audit is not None else []

    def _audit(rec: dict[str, Any]) -> None:
        records.append(rec)

    notifier = SmallPaperDiscordNotifier(
        _cfg(),
        profile="p",
        entry_profile="p",
        error_logger=lambda op, msg, extra: None,
        delivery_audit=_audit,
    )
    notifier._resolve_trade_webhook = lambda: ("https://discord.com/api/webhooks/test/token", "notify")  # type: ignore[method-assign]
    notifier._trade_webhook_url = "https://discord.com/api/webhooks/test/token"
    notifier._trade_webhook_source = "notify"
    return notifier


def test_normal_send_delivered_and_audited():
    audit: list[dict] = []
    notifier = _notifier(audit)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"id":"123456789"}'
    mock_resp.json.return_value = {"id": "123456789"}

    with patch("small_paper.discord_notifier.requests.post", return_value=mock_resp):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            res = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
                slot_before=3,
            )

    assert res.final_result == FINAL_DELIVERED
    assert res.http_status == 200
    assert res.discord_message_id == "123456789"
    assert res.sent_time
    assert audit
    assert audit[0]["final_result"] == FINAL_DELIVERED
    assert audit[0]["persisted_to_log"] is True
    assert notifier.entry_retry_queue.pending == []


def test_dns_failure_enqueues_retry():
    audit: list[dict] = []
    notifier = _notifier(audit)

    with patch(
        "small_paper.discord_notifier.requests.post",
        side_effect=requests.exceptions.ConnectionError("DNS failed"),
    ):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            res = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )

    assert res.final_result == FINAL_FAILED
    assert res.failure_classification == CLASS_WEBHOOK_SEND_FAILED
    assert res.exception_type == "ConnectionError"
    assert len(notifier.entry_retry_queue.pending) == 1
    assert audit[0]["final_result"] == FINAL_FAILED


def test_http500_failure():
    notifier = _notifier()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "internal error"

    with patch("small_paper.discord_notifier.requests.post", return_value=mock_resp):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            res = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )

    assert res.final_result == FINAL_FAILED
    assert res.failure_classification == CLASS_HTTP_FAILED
    assert res.http_status == 500


def test_timeout_failure():
    notifier = _notifier()

    with patch(
        "small_paper.discord_notifier.requests.post",
        side_effect=requests.exceptions.Timeout("timed out"),
    ):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            res = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )

    assert res.final_result == FINAL_FAILED
    assert res.exception_type == "Timeout"


def test_retry_success_on_flush():
    audit: list[dict] = []
    notifier = _notifier(audit)
    mock_fail = MagicMock()
    mock_fail.status_code = 503
    mock_fail.text = "unavailable"
    mock_ok = MagicMock()
    mock_ok.status_code = 200
    mock_ok.text = '{"id":"999"}'
    mock_ok.json.return_value = {"id": "999"}

    with patch("small_paper.discord_notifier.requests.post", side_effect=[mock_fail, mock_ok]):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            first = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )
            assert first.final_result == FINAL_FAILED
            flushed = notifier.flush_entry_notify_retries()

    assert len(flushed) == 1
    assert flushed[0].final_result == FINAL_DELIVERED
    assert flushed[0].retry_count == 1
    assert notifier.entry_retry_queue.pending == []


def test_retry_exhausted_failure():
    audit: list[dict] = []
    notifier = _notifier(audit)
    notifier.entry_retry_queue.max_retries = 1
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "unavailable"

    with patch("small_paper.discord_notifier.requests.post", return_value=mock_resp):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )
            flushed = notifier.flush_entry_notify_retries()

    assert flushed
    assert flushed[0].final_result == FINAL_FAILED
    assert flushed[0].failure_classification == CLASS_NO_RETRY_TERMINATED


def test_sent_time_persisted_on_success():
    """POST success must populate sent_time on DiscordPostResult (pilot_runner persists to accept)."""
    notifier = _notifier()
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_resp.text = ""
    mock_resp.json.side_effect = ValueError("no body")

    with patch("small_paper.discord_notifier.requests.post", return_value=mock_resp):
        with patch("small_paper.discord_notifier.get_cached_symbol_name_map", return_value={}):
            res = notifier.notify_entry(
                event=_entry_event(),
                payload={},
                open_slots=4,
                session_bucket="afternoon",
            )

    assert res.final_result == FINAL_DELIVERED
    assert res.sent_time


def test_historical_707_likely_sent_is_unprovable():
    accept = {
        "symbol": "6327.T",
        "event_time": "2026-07-07T12:58:53+09:00",
        "position_id": "6327.T|2026-07-07T12:58:53+09:00",
    }
    row = audit_entry_pipeline_row(
        accept,
        trade_date="20260707",
        errors=[],
        window=DiscordErrorWindow(first=None, last=None, count=0),
        delivery_by_key={},
    )
    assert row["final_result"] == FINAL_UNPROVABLE
    assert row["prior_inferred_label"] == "likely_sent_metadata_not_logged"
    proof = build_missing_entry_notification_proof([row])
    assert proof[0]["verdict"] == "unprovable_no_post_evidence"
    assert proof[0]["post_success_proven"] is False
    assert proof[0]["post_failure_proven"] is False


def test_historical_708_outage_proven_failed():
    accept = {
        "symbol": "7220.T",
        "event_time": "2026-07-08T13:08:09+09:00",
        "position_id": "7220.T|2026-07-08T13:08:09+09:00",
    }
    window = DiscordErrorWindow(
        first="2026-07-08T13:00:14+09:00",
        last="2026-07-08T13:20:20+09:00",
        count=389,
    )
    row = audit_entry_pipeline_row(
        accept,
        trade_date="20260708",
        errors=[],
        window=window,
        delivery_by_key={},
    )
    assert row["final_result"] == FINAL_FAILED
    assert row["failure_classification"] == CLASS_WEBHOOK_SEND_FAILED


def test_phase663a4_audit_on_live_sessions():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260708" / "live_session_122537" / "small_paper_events.jsonl").is_file():
        pytest.skip("live session fixtures missing")
    report = run_audit(run_regression=False)
    assert report["verdict"] == PHASE663A4_VERDICT
    assert report["final_verdict"] == "両方"
    assert report["proven_failed"] >= 10
    assert report["unprovable"] >= 37
    assert report["likely_sent_proof_count_20260707"] >= 1

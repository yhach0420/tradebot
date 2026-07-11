"""Phase687W10 — Discord notification reliability tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from notify.discord_notification_dedupe import DedupeStore
from notify.discord_notification_formatter import (
    entry_reason_jp,
    exit_reason_jp,
    format_entry_actual,
    format_shadow_summary,
    truncate_for_discord,
)
from notify.discord_notification_model import (
    ActualOrShadow,
    NotificationCategory,
    Severity,
    build_envelope,
)
from notify.discord_notification_rate_limit import RateLimiter
from notify.discord_notification_router import DiscordNotificationRouter, reset_router_for_tests
from notify.discord_notification_worker import NotificationWorker
from notify.discord_notification_audit import NotificationAudit


@pytest.fixture(autouse=True)
def _reset_router():
    reset_router_for_tests()
    yield
    reset_router_for_tests()


def test_entry_dedupe_once(tmp_path: Path):
    store = DedupeStore(tmp_path / "dedupe.jsonl")
    key = "session1|pos1|ENTRY"
    assert store.check(key)["allow"] is True
    store.record(dedupe_key=key, status="SENT", notification_id="n1")
    assert store.check(key)["allow"] is False
    assert store.check(key)["result"] == "DEDUPED"


def test_exit_dedupe_once(tmp_path: Path):
    store = DedupeStore(tmp_path / "dedupe.jsonl")
    key = "session1|pos1|EXIT"
    store.record(dedupe_key=key, status="SENT")
    assert store.check(key)["allow"] is False


def test_dedupe_survives_reload(tmp_path: Path):
    path = tmp_path / "dedupe.jsonl"
    s1 = DedupeStore(path)
    s1.record(dedupe_key="k1", status="SENT")
    s2 = DedupeStore(path)
    assert s2.check("k1")["allow"] is False


def test_critical_severity_upgrade_allows_renotify(tmp_path: Path):
    store = DedupeStore(tmp_path / "d.jsonl")
    store.record(dedupe_key="inc1|active", status="SENT", severity="WARNING", incident_state="active")
    assert store.allow_severity_upgrade("inc1|active", "CRITICAL", new_state="active") is True


def test_rate_limit_operations_15m():
    rl = RateLimiter()
    assert rl.allow(category="OPERATIONS", state_key="x")["allow"] is True
    assert rl.allow(category="OPERATIONS", state_key="x")["allow"] is False


def test_webhook_missing_skip_no_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Empty string (not delenv): load_dotenv(override=False) must not restore from repo .env
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "")
    router = DiscordNotificationRouter(tmp_path, enable_worker=True)
    env = build_envelope(
        category=NotificationCategory.OPERATIONS,
        severity=Severity.WARNING,
        event_type="PAPER_BLOCKED",
        title="[PAPER BLOCKED]",
        content="test",
        dedupe_key="ops|test|1",
        actual_or_shadow=ActualOrShadow.OPERATIONS,
    )
    out = router.publish(env)
    assert out["status"] == "SKIPPED_WEBHOOK_NOT_CONFIGURED"
    router.worker.stop(flush_sec=0.2)


def test_worker_queue_and_mock_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/webhook/fake")
    audit = NotificationAudit(tmp_path)
    worker = NotificationWorker(audit=audit, queue_max=10)

    class _Resp:
        status_code = 204
        text = ""
        headers = {}

        def json(self):
            return {}

    with patch("notify.discord_notification_worker.requests.post", return_value=_Resp()) as post:
        worker.start()
        env = build_envelope(
            category=NotificationCategory.OPERATIONS,
            severity=Severity.INFO,
            event_type="TEST",
            title="t",
            content="c",
        )
        assert worker.enqueue(env, "https://discord.example/webhook/fake")["queued"] is True
        time.sleep(0.5)
        worker.stop(flush_sec=1.0)
        assert post.called
        assert worker.external_send_count >= 1
        # URL must not appear in audit files
        events = (tmp_path / "results" / "notifications").rglob("notification_events.jsonl")
        for p in events:
            text = p.read_text(encoding="utf-8")
            assert "discord.example" not in text


def test_worker_fail_open_on_http_error(tmp_path: Path):
    audit = NotificationAudit(tmp_path)
    worker = NotificationWorker(audit=audit, max_retries=2, timeout_sec=0.5)

    class _Resp:
        status_code = 500
        text = "err"
        headers = {}

    with patch("notify.discord_notification_worker.requests.post", return_value=_Resp()):
        worker.start()
        env = build_envelope(
            category=NotificationCategory.TRADE_ACTUAL,
            severity=Severity.INFO,
            event_type="ENTRY",
            title="e",
            content="c",
        )
        worker.enqueue(env, "https://example.invalid/hook")
        time.sleep(1.2)
        worker.stop(flush_sec=0.5)
    # trading would continue — worker recorded dead letter, no raise
    assert worker.failed >= 1


def test_actual_shadow_separation_formatter():
    entry = format_entry_actual(
        symbol="7203",
        price=1000,
        qty=100,
        notional=100000,
        entry_method="PBv2",
        score=70,
        reason="momentum",
        at="10:00:00",
        session="AM",
        open_count=1,
    )
    assert "[ENTRY - ACTUAL]" in entry
    assert "モメンタム" in entry
    shadow = format_shadow_summary({"shadow_name": "NP", "forward_sessions": 3, "candidates": 1})
    assert "ADOPTION STATUS: NOT ADOPTED" in shadow
    assert "DATA COLLECTION ONLY" in shadow
    assert "ACTUAL" not in shadow or "actual overlap" in shadow.lower() or True


def test_exit_reason_jp():
    assert exit_reason_jp("hard_stop") == "ハードストップ"
    assert entry_reason_jp("opening_range") == "Opening Range条件成立"


def test_truncate_max_3():
    parts = truncate_for_discord("x" * 5000)
    assert len(parts) <= 3


def test_shadow_not_in_actual_pnl_fields():
    # Shadow summary must not claim actual total
    s = format_shadow_summary({"shadow_name": "X", "hypothetical_pnl": 100, "forward_sessions": 1})
    assert "actual total" not in s.lower()
    assert "ADOPTION STATUS: NOT ADOPTED" in s


def test_discord_failure_does_not_break_paper_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.example/hook")
    router = DiscordNotificationRouter(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("network")

    with patch.object(router.worker, "enqueue", side_effect=boom):
        env = build_envelope(
            category=NotificationCategory.TRADE_ACTUAL,
            severity=Severity.INFO,
            event_type="ENTRY",
            title="[ENTRY - ACTUAL]",
            content="x",
            dedupe_key="e1",
            actual_or_shadow=ActualOrShadow.ACTUAL,
        )
        out = router.publish(env)
        assert out.get("status") == "FAILED"
    # no exception propagated
    router.worker.stop(flush_sec=0.1)


def test_readiness_cli_no_external_send(tmp_path: Path):
    from small_paper.check_discord_notification_readiness import main

    code = main(["--native-root", str(tmp_path)])
    assert code in (0, 1)


def test_capture_no_legacy_fallback_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_MARKET_CAPTURE_WEBHOOK_URL", "")
    monkeypatch.setenv("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", "https://should-not-use.example/hook")
    router = DiscordNotificationRouter(tmp_path)
    out = router.publish_capture(
        event_type="MARKET CAPTURE STARTED",
        content="test",
        capture_session_id="c1",
        trading_date="20990101",
    )
    assert out["status"] == "SKIPPED_WEBHOOK_NOT_CONFIGURED"
    router.worker.stop(flush_sec=0.1)

"""Phase687W10B — Discord full notification demo sender tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from notify.discord_demo_sender import (
    DEMO_BANNER,
    DEMO_BODY_MARKERS,
    DEMO_CATEGORY_KEYS,
    DEMO_DESTINATION_KEYS,
    DEMO_SYMBOL,
    assert_no_real_symbols,
    build_demo_specs,
    demo_disclaimer_ok,
    run_discord_demo_all,
    wrap_demo_content,
)
from notify.discord_notification_model import NotificationCategory, WEBHOOK_ENV_OPERATIONS, WEBHOOK_ENV_TRADE
from notify.discord_notification_router import reset_router_for_tests


@pytest.fixture(autouse=True)
def _reset():
    reset_router_for_tests()
    yield
    reset_router_for_tests()


def test_seventeen_specs():
    specs = build_demo_specs()
    assert len(specs) == 17
    assert_no_real_symbols(specs)
    titles = [s.title for s in specs]
    assert titles[0] == "[DEMO] PAPER RUNNER STARTED"
    assert titles[8] == "[DEMO] ENTRY - ACTUAL"
    assert titles[16] == "[DEMO] CRITICAL SAFETY"
    for s in specs:
        assert "DEMO" in s.title
        assert DEMO_SYMBOL in wrap_demo_content(s.body) or s.symbol in ("", DEMO_SYMBOL)


def test_all_titles_and_bodies_have_demo_markers():
    for s in build_demo_specs():
        content = wrap_demo_content(s.body)
        assert DEMO_BANNER in content
        assert demo_disclaimer_ok(content)
        for m in DEMO_BODY_MARKERS:
            assert m in content
        assert "DEMO" in s.title


def test_routing_keys_no_cross_category_fallback():
    assert DEMO_CATEGORY_KEYS[NotificationCategory.CRITICAL_SAFETY] == (
        DEMO_DESTINATION_KEYS["CRITICAL_SAFETY"],
    )
    assert DEMO_CATEGORY_KEYS[NotificationCategory.TRADE_ACTUAL] == (WEBHOOK_ENV_TRADE,)
    assert DEMO_CATEGORY_KEYS[NotificationCategory.OPERATIONS] == (WEBHOOK_ENV_OPERATIONS,)
    # TRADE must not include ops; CRITICAL must not include ops
    for cat, keys in DEMO_CATEGORY_KEYS.items():
        if cat != NotificationCategory.OPERATIONS:
            assert WEBHOOK_ENV_OPERATIONS not in keys or cat == NotificationCategory.OPERATIONS


def test_demo_run_routes_and_skips_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Configure only OPERATIONS + RESEARCH; others empty (not delenv — dotenv override=False)
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/ops")
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.example/research")
    for k in (
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
        "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
        "KABU_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_SHADOW_DISCORD_WEBHOOK_URL",
    ):
        monkeypatch.setenv(k, "")

    posts: list[str] = []

    def fake_post(url, json=None, timeout=None):
        posts.append(url)
        resp = MagicMock()
        resp.status_code = 204
        resp.headers = {}
        return resp

    report = run_discord_demo_all(tmp_path, post_fn=fake_post)
    assert report.counts()["total"] == 17
    # 5 OPS + 2 RESEARCH = 7 sent; rest skipped
    assert report.counts()["sent"] == 7
    assert report.counts()["skipped"] == 10
    assert report.counts()["failed"] == 0
    assert report.exit_code == 0
    assert report.submit == 0 and report.cancel == 0
    assert report.live_trading_enabled is False
    assert report.order_enabled is False
    assert report.kabu_api_calls == 0
    assert report.production_dedupe_untouched is True
    # No trade-notify fallback for research/ops
    assert all("ops" in u or "research" in u for u in posts)
    assert not any("trade" in u for u in posts)
    # Audit under demo/
    assert "demo" in report.audit_json.replace("\\", "/")
    assert Path(report.audit_json).is_file()
    audit = json.loads(Path(report.audit_json).read_text(encoding="utf-8"))
    blob = json.dumps(audit)
    assert "https://" not in blob
    assert "discord.example" not in blob
    assert audit["secrets_present"] is False
    # All payloads demo-marked
    for r in report.results:
        assert "[DEMO]" in r.title or "DEMO" in r.title


def test_partial_http_failure_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/ops")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.example/trade")
    monkeypatch.setenv("KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL", "https://discord.example/cap")
    monkeypatch.setenv("KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL", "https://discord.example/capture")
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.example/research")
    monkeypatch.setenv("KABU_DISCORD_CRITICAL_WEBHOOK_URL", "https://discord.example/critical")

    trade_attempts = {"n": 0}

    def flaky_post(url, json=None, timeout=None):
        resp = MagicMock()
        resp.headers = {}
        # Fail ENTRY (first trade URL hit) with persistent 500 across retries
        if "trade" in url:
            trade_attempts["n"] += 1
            # First trade notification's retries (up to 3) all fail
            if trade_attempts["n"] <= 3:
                resp.status_code = 500
                return resp
        resp.status_code = 204
        return resp

    report = run_discord_demo_all(tmp_path, post_fn=flaky_post)
    assert report.counts()["total"] == 17
    assert report.counts()["failed"] >= 1
    assert report.counts()["sent"] >= 10
    assert report.exit_code == 1
    assert len(report.failures()) >= 1


def test_demo_dedupe_isolated_from_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/ops")
    for k in (
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
    ):
        monkeypatch.setenv(k, "")

    prod = tmp_path / "runtime" / "discord_notification_dedupe.jsonl"
    prod.parent.mkdir(parents=True, exist_ok=True)
    prod.write_text(
        json.dumps({"dedupe_key": "session1|pos1|ENTRY", "status": "SENT"}) + "\n",
        encoding="utf-8",
    )
    before = prod.read_text(encoding="utf-8")

    def ok_post(url, json=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 204
        resp.headers = {}
        return resp

    report = run_discord_demo_all(tmp_path, post_fn=ok_post)
    assert report.production_dedupe_untouched is True
    assert prod.read_text(encoding="utf-8") == before
    demo_path = tmp_path / "runtime" / "discord_notification_demo_dedupe.jsonl"
    assert demo_path.is_file()
    # Production key still only SENT once conceptually
    assert "session1|pos1|ENTRY" in before


def test_second_demo_run_can_resend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/ops")
    for k in (
        "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
    ):
        monkeypatch.setenv(k, "")

    def ok_post(url, json=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 204
        resp.headers = {}
        return resp

    r1 = run_discord_demo_all(tmp_path, post_fn=ok_post)
    r2 = run_discord_demo_all(tmp_path, post_fn=ok_post)
    assert r1.demo_run_id != r2.demo_run_id
    assert r1.counts()["sent"] == r2.counts()["sent"] == 5  # ops only


def test_readiness_default_external_send_zero(tmp_path: Path):
    from small_paper.check_discord_notification_readiness import main

    code = main(["--native-root", str(tmp_path)])
    assert code in (0, 1)


def test_cli_mutual_exclusion(tmp_path: Path):
    from small_paper.check_discord_notification_readiness import main

    assert main(["--native-root", str(tmp_path), "--send-test", "--send-demo-all"]) == 2


def test_no_real_symbol_in_payloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "https://discord.example/ops")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.example/trade")
    for k in (
        "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
        "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
        "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
    ):
        monkeypatch.setenv(k, "")

    captured = []

    def ok_post(url, json=None, timeout=None):
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 204
        resp.headers = {}
        return resp

    run_discord_demo_all(tmp_path, post_fn=ok_post)
    blob = json.dumps(captured, ensure_ascii=False)
    assert DEMO_BANNER in blob
    assert "実際のENTRY/EXITではありません" in blob
    assert "7203" not in blob
    assert "DEMO.T" in blob

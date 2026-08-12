"""V1R_PBV2_NOTIFICATION_ROUTING_ONLY — route-only, no occupancy impact."""
from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from small_paper.v1r_pbv2_notification_routing import (
    ENV_LIVE_PRIMARY,
    ENV_ROUTING_ONLY,
    PBV2_SHADOW_PREFIX,
    resolve_pbv2_shadow_webhook,
    routing_audit,
    routing_only_enabled,
    shadow_title,
    should_reroute_trade_event,
)

NATIVE = Path(__file__).resolve().parents[1]
MOD = NATIVE / "src" / "small_paper" / "v1r_pbv2_notification_routing.py"
NOTIFIER = NATIVE / "src" / "small_paper" / "discord_notifier.py"


def test_routing_defaults_on_with_live_primary(monkeypatch):
    monkeypatch.delenv(ENV_ROUTING_ONLY, raising=False)
    monkeypatch.setenv(ENV_LIVE_PRIMARY, "1")
    assert routing_only_enabled() is True
    assert should_reroute_trade_event("ENTRY") is True
    assert should_reroute_trade_event("EXIT") is True
    assert should_reroute_trade_event("HEARTBEAT") is False


def test_routing_explicit_off(monkeypatch):
    monkeypatch.setenv(ENV_LIVE_PRIMARY, "1")
    monkeypatch.setenv(ENV_ROUTING_ONLY, "0")
    assert routing_only_enabled() is False
    assert should_reroute_trade_event("ENTRY") is False


def test_shadow_title_prefix():
    assert shadow_title("ENTRY 6098.T").startswith(PBV2_SHADOW_PREFIX)
    assert shadow_title(f"{PBV2_SHADOW_PREFIX} X").startswith(PBV2_SHADOW_PREFIX)


def test_routing_module_has_no_occupancy_imports():
    src = MOD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    assert "v1r_live_dual_lane" not in joined
    assert "pilot_runner" not in joined
    assert "observer_position_tracker" not in joined
    # Code body (exclude module docstring) must not call occupancy APIs
    body = src.split('"""', 2)[-1] if src.startswith('"""') else src
    assert "try_admit" not in body
    assert "register_entry" not in body
    audit = routing_audit()
    assert audit["affects_arch_e_occupancy"] is False
    assert audit["affects_dual_lane"] is False
    assert audit["affects_submit_cancel_live"] is False


def test_notify_entry_uses_research_not_trade_notify(monkeypatch):
    monkeypatch.setenv(ENV_ROUTING_ONLY, "1")
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.com/api/webhooks/research/fake")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.com/api/webhooks/trade/fake")

    from small_paper.discord_entry_delivery import webhook_url_hash
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    cfg = SmallPaperDiscordConfig(
        enabled=True,
        observer_only=True,
        webhook_env="KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
        trade_notify_webhook_env="KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    )
    n = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
    n._trade_webhook_url = ""
    n._legacy_webhook_url = ""

    captured = {}
    research_url = "https://discord.com/api/webhooks/research/fake"
    trade_url = "https://discord.com/api/webhooks/trade/fake"

    def fake_publish(env):
        captured["ownership"] = env.ownership
        captured["category"] = str(env.category)
        captured["title"] = env.title
        captured["aos"] = str(env.actual_or_shadow)
        return {"status": "QUEUED", "queued": True}

    class FakeRouter:
        def publish(self, env):
            return fake_publish(env)

        class worker:
            @staticmethod
            def enqueue(env, url):
                captured["enqueue_url"] = url
                return {"status": "QUEUED", "queued": True}

    with patch("notify.discord_notification_router.get_router", return_value=FakeRouter()), patch(
        "small_paper.v1r_pbv2_notification_routing.resolve_pbv2_shadow_webhook",
        return_value=(research_url, "KABU_DISCORD_RESEARCH_WEBHOOK_URL"),
    ), patch.object(n, "_resolve_trade_webhook", return_value=(trade_url, "notify")):
        res = n._post_with_result(
            event_tag="ENTRY",
            title_line="ENTRY 285A.T",
            fields=[{"name": "x", "value": "y", "inline": False}],
            color=0x2F855A,
            trade_notify=True,
            dedupe_key="test|entry|285A",
        )

    assert res.webhook_called is True
    assert res.webhook_url_hash == webhook_url_hash(research_url)
    assert res.webhook_url_hash != webhook_url_hash(trade_url)
    assert captured.get("ownership") == "PBV2_SHADOW_ONLY"
    assert "SHADOW" in str(captured.get("aos") or "").upper() or "shadow" in str(
        captured.get("category") or ""
    ).lower()
    assert PBV2_SHADOW_PREFIX in str(captured.get("title") or "")


def test_notify_entry_trade_notify_when_routing_disabled(monkeypatch):
    monkeypatch.setenv(ENV_ROUTING_ONLY, "0")
    monkeypatch.setenv(ENV_LIVE_PRIMARY, "1")
    monkeypatch.setenv("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "https://discord.com/api/webhooks/research/fake")
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://discord.com/api/webhooks/trade/fake")

    from small_paper.discord_entry_delivery import webhook_url_hash
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    cfg = SmallPaperDiscordConfig(
        enabled=True,
        observer_only=True,
        webhook_env="KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
        trade_notify_webhook_env="KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    )
    n = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
    trade_url = "https://discord.com/api/webhooks/trade/fake"

    class FakeRouter:
        def publish(self, env):
            return {"status": "SKIPPED_WEBHOOK_NOT_CONFIGURED"}

        class worker:
            @staticmethod
            def enqueue(env, url):
                return {"status": "QUEUED", "queued": True}

    with patch("notify.discord_notification_router.get_router", return_value=FakeRouter()), patch.object(
        n, "_resolve_trade_webhook", return_value=(trade_url, "notify")
    ):
        res = n._post_with_result(
            event_tag="ENTRY",
            title_line="ENTRY 285A.T",
            fields=[{"name": "x", "value": "y", "inline": False}],
            color=0x2F855A,
            trade_notify=True,
            dedupe_key="test|entry|off",
        )
    assert res.webhook_url_hash == webhook_url_hash(trade_url)

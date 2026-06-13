"""trade-cap-blocked Discord channel routing."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from small_paper.discord_message_builder import build_entry_cap_blocked_detail
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier


class TestDiscordCapBlockedNotify(unittest.TestCase):
    def test_build_entry_cap_blocked_detail(self) -> None:
        detail = build_entry_cap_blocked_detail(
            symbol="6981.T",
            entry_score_v2=3,
            data={
                "trading_value": 1_000_000,
                "entry_expectancy_score_v2": 3,
            },
            active_positions=3,
            position_cap=3,
        )
        self.assertIn("6981.T", detail)
        self.assertIn("ENTRY条件成立", detail)
        self.assertIn("active_positions: 3", detail)
        self.assertIn("position_cap: 3", detail)
        self.assertIn("保有上限到達", detail)
        self.assertIn("entry_score_v2: 3", detail)
        self.assertIn("ENTRY理由:", detail)
        self.assertNotIn("保有中:", detail)

    def test_notify_uses_cap_blocked_webhook_not_trade_notify(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_entry_cap_blocked=True,
            entry_deferred_cooldown_sec=0.0,
            trade_notify_webhook_env="KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
            trade_cap_blocked_webhook_env="KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        posted: list[dict] = []

        def fake_post(**kwargs: object) -> bool:
            posted.append(dict(kwargs))
            return True

        env = {
            "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL": "https://discord.test/trade-notify",
            "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL": "https://discord.test/cap-blocked",
            "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL": "https://discord.test/legacy",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(notifier, "_post", side_effect=fake_post):
                ok = notifier.notify_entry_cap_blocked(
                    event={
                        "symbol": "6981.T",
                        "event_time": "2026-06-05T09:15:00+09:00",
                        "entry_expectancy_score_v2": 3,
                    },
                    payload={"CurrentPrice": 1200.0},
                    trade_data={"entry_expectancy_score_v2": 3, "trading_value": 500_000},
                    open_slots=3,
                )
        self.assertTrue(ok)
        self.assertEqual(len(posted), 1)
        self.assertTrue(posted[0].get("cap_blocked"))
        self.assertFalse(posted[0].get("trade_notify"))
        self.assertEqual(posted[0]["event_tag"], "CAP BLOCKED")

    def test_notify_all_scores_not_only_score5(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_entry_cap_blocked=True,
            entry_deferred_cooldown_sec=0.0,
            entry_deferred_min_score_v2=5,
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        posted: list[dict] = []

        with patch.object(notifier, "_post", side_effect=lambda **kw: posted.append(kw) or True):
            with patch.object(notifier, "_resolve_cap_blocked_webhook", return_value="https://x"):
                ok = notifier.notify_entry_cap_blocked(
                    event={"symbol": "7203.T", "entry_expectancy_score_v2": 3},
                    payload={},
                    trade_data={"entry_expectancy_score_v2": 3},
                    open_slots=3,
                )
        self.assertTrue(ok)
        detail = posted[0]["fields"][0]["value"]
        self.assertIn("entry_score_v2: 3", detail)


if __name__ == "__main__":
    unittest.main()

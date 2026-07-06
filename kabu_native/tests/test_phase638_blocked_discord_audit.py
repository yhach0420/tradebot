"""Phase638: blocked ENTRY Discord notification audit + regression tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from research.exposure_gate import GateDecision
from small_paper.discord_message_builder import build_entry_cap_blocked_detail
from small_paper.discord_notifier import (
    SmallPaperDiscordConfig,
    SmallPaperDiscordNotifier,
    discord_notify_summary_fields,
)
from small_paper.reject_reasons import (
    REJECT_MAX_CONCURRENT,
    REJECT_MAX_ENTRIES_PER_SCAN,
    REJECT_OR_CAP_FULL,
    REJECT_SAME_SYMBOL_OPEN_OVERLAP,
    is_entry_blocked_discord_notify_reason,
)


class Phase638BlockedDiscordTests(unittest.TestCase):
    def test_entry_blocked_reasons_set(self) -> None:
        for reason in (
            REJECT_MAX_CONCURRENT,
            REJECT_SAME_SYMBOL_OPEN_OVERLAP,
            REJECT_MAX_ENTRIES_PER_SCAN,
            REJECT_OR_CAP_FULL,
            "pbv2_cap_full",
        ):
            self.assertTrue(is_entry_blocked_discord_notify_reason(reason))
        self.assertFalse(is_entry_blocked_discord_notify_reason("data_stale_board"))

    def test_cap_blocked_detail_shows_block_reason(self) -> None:
        detail = build_entry_cap_blocked_detail(
            symbol="6981.T",
            entry_score_v2=4,
            data={"entry_expectancy_score_v2": 4},
            active_positions=4,
            position_cap=4,
            block_reason=REJECT_OR_CAP_FULL,
        )
        self.assertIn("OR枠上限到達", detail)
        self.assertIn("or_cap_full", detail)

    def test_cap_blocked_does_not_require_trade_notify_active(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_entry_cap_blocked=True,
            entry_deferred_cooldown_sec=0.0,
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        self.assertFalse(notifier.active)
        self.assertTrue(notifier.cap_blocked_notify_enabled())
        posted: list[dict] = []

        def fake_post(**kwargs: object) -> bool:
            posted.append(dict(kwargs))
            return True

        env = {"KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL": "https://discord.test/cap-blocked"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(notifier, "_post", side_effect=fake_post):
                ok = notifier.notify_entry_cap_blocked(
                    event={
                        "symbol": "6981.T",
                        "event_time": "2026-06-05T09:15:00+09:00",
                        "gate_reject_reason": REJECT_OR_CAP_FULL,
                    },
                    payload={"CurrentPrice": 1200.0},
                    trade_data={"entry_expectancy_score_v2": 3},
                    open_slots=1,
                    block_reason=REJECT_OR_CAP_FULL,
                )
        self.assertTrue(ok)
        self.assertEqual(notifier.cap_blocked_notify_sent_count, 1)
        self.assertTrue(posted[0].get("cap_blocked"))
        self.assertFalse(posted[0].get("trade_notify"))

    def test_missing_cap_webhook_logs_error_no_trade_notify(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_entry_cap_blocked=True,
            trade_notify_webhook_env="KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
            entry_deferred_cooldown_sec=0.0,
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        env = {
            "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL": "https://discord.test/trade-notify",
        }
        with patch.dict(os.environ, env, clear=True):
            os.environ.pop("KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL", None)
            ok = notifier.notify_entry_cap_blocked(
                event={"symbol": "6981.T", "gate_reject_reason": REJECT_MAX_CONCURRENT},
                payload={},
                trade_data={},
                open_slots=3,
            )
        self.assertFalse(ok)
        self.assertEqual(notifier.discord_error_count, 1)
        self.assertEqual(notifier.cap_blocked_notify_sent_count, 0)
        self.assertFalse(notifier.cap_blocked_channel_ready())

    def test_discord_failure_increments_error_count(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_entry_cap_blocked=True,
            entry_deferred_cooldown_sec=0.0,
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")

        def fail_post(**kwargs: object) -> bool:
            return False

        env = {"KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL": "https://discord.test/cap-blocked"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(notifier, "_post", side_effect=fail_post):
                ok = notifier.notify_entry_cap_blocked(
                    event={"symbol": "6981.T", "gate_reject_reason": REJECT_MAX_CONCURRENT},
                    payload={},
                    trade_data={},
                    open_slots=3,
                )
        self.assertFalse(ok)
        self.assertEqual(notifier.cap_blocked_notify_attempt_count, 1)
        self.assertEqual(notifier.cap_blocked_notify_sent_count, 0)

    def test_summary_fields_include_discord_health(self) -> None:
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True, send_entry_cap_blocked=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        notifier.discord_error_count = 2
        notifier.cap_blocked_notify_attempt_count = 5
        notifier.cap_blocked_notify_sent_count = 3
        fields = discord_notify_summary_fields(notifier)
        self.assertEqual(fields["discord_error_count"], 2)
        self.assertEqual(fields["cap_blocked_notify_sent_count"], 3)
        self.assertEqual(fields["cap_blocked_notify_attempt_count"], 5)

    def test_notify_rejected_skips_entry_blocked_reasons(self) -> None:
        cfg = SmallPaperDiscordConfig(
            enabled=True,
            observer_only=True,
            send_rejects=True,
            send_entry_cap_blocked=True,
        )
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        notifier._resolve_trade_webhook = lambda: ("https://x", "notify")  # type: ignore[method-assign]
        notifier._resolve_legacy_webhook = lambda: "https://x"  # type: ignore[method-assign]
        posted: list[dict] = []

        def fake_post(**kwargs: object) -> bool:
            posted.append(dict(kwargs))
            return True

        with patch.object(notifier, "_post", side_effect=fake_post):
            ok = notifier.notify_rejected(
                event={"symbol": "6981.T", "gate_reject_reason": REJECT_OR_CAP_FULL},
                payload={},
                open_slots=1,
                session_bucket="AM",
            )
        self.assertFalse(ok)
        self.assertEqual(len(posted), 0)


if __name__ == "__main__":
    unittest.main()

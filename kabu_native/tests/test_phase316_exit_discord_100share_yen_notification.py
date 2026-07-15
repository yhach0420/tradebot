"""Phase316: EXIT Discord notification includes 100-share yen PnL."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from notify.discord import ShadowDiscordConfig, ShadowDiscordNotifier
from replay.pnl_yen import format_exit_pnl_line, format_pnl_yen_100_display, resolve_pnl_yen_100
from small_paper.discord_message_builder import build_exit_detail, build_entry_detail


class TestPhase316ExitDiscordYenNotification(unittest.TestCase):
    def test_format_pnl_yen_100_display(self) -> None:
        self.assertEqual(format_pnl_yen_100_display(1200.0), "+1,200円(100株)")
        self.assertEqual(format_pnl_yen_100_display(-850.4), "-850円(100株)")

    def test_format_exit_pnl_line_with_yen(self) -> None:
        self.assertEqual(
            format_exit_pnl_line(0.42, 1200.0),
            "損益: +0.42% / +1,200円(100株)",
        )

    def test_format_exit_pnl_line_without_yen(self) -> None:
        self.assertEqual(format_exit_pnl_line(0.42, None), "損益: +0.42%")

    def test_build_exit_detail_includes_yen(self) -> None:
        detail = build_exit_detail(
            symbol="3905.T",
            entry_price=2857.0,
            exit_price=2869.0,
            pnl_pct=0.42,
            mfe_pct=0.8,
            mae_pct=-0.2,
            hold_minutes=12.0,
            exit_reason="trailing_mfe_exit",
            pnl_yen_100=1200.0,
        )
        self.assertIn("損益: +0.42% / +1,200円(100株)", detail)

    def test_build_exit_detail_computes_yen_from_prices(self) -> None:
        detail = build_exit_detail(
            symbol="7203.T",
            entry_price=2800.0,
            exit_price=2812.0,
            pnl_pct=0.43,
            mfe_pct=0.5,
            mae_pct=-0.1,
            hold_minutes=8.0,
            exit_reason="momentum_fade_exit",
        )
        self.assertIn("損益: +0.43% / +1,200円(100株)", detail)

    def test_build_exit_detail_pct_only_when_prices_missing(self) -> None:
        detail = build_exit_detail(
            symbol="9984.T",
            entry_price=0.0,
            exit_price=0.0,
            pnl_pct=-0.5,
            mfe_pct=None,
            mae_pct=None,
            hold_minutes=5.0,
            exit_reason="hard_stop",
            pnl_yen_100=None,
        )
        self.assertIn("損益: -0.50%", detail)
        self.assertNotIn("円(100株)", detail)

    def test_entry_detail_unchanged(self) -> None:
        detail = build_entry_detail(
            symbol="3905.T",
            entry_price=4520.0,
            stop_price=4465.76,
            slot_usage="2/3",
            entry_score_v2=3,
            data={"entry_expectancy_score_v2": 3},
        )
        self.assertIn("価格:", detail)
        self.assertNotIn("円(100株)", detail)

    def test_resolve_pnl_yen_100_prefers_explicit(self) -> None:
        self.assertEqual(
            resolve_pnl_yen_100(entry_price=100.0, exit_price=200.0, pnl_yen_100=999.0),
            999.0,
        )

    @patch.object(ShadowDiscordNotifier, "_post_embed", return_value=True)
    def test_notify_paper_exit_shows_yen_line(self, mock_post) -> None:
        notifier = ShadowDiscordNotifier(
            ShadowDiscordConfig(
                enabled=True,
                shadow_notify=True,
                paper_trade_notify=True,
                dedupe=False,
                cooldown_sec=0.0,
            )
        )
        notifier._webhook_url = "http://example.invalid/webhook"
        ok = notifier.notify_paper_exit(
            symbol="3905.T",
            entry_price=2857.0,
            exit_price=2869.0,
            entry_time=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc),
            exit_reason="trailing_mfe_exit",
            pnl_pct=0.42,
            mfe_pct=0.8,
            elapsed_min=12.0,
            pnl_yen_100=1200.0,
        )
        self.assertTrue(ok)
        fields = mock_post.call_args.kwargs["fields"]
        pnl_field = next(f for f in fields if f["name"] == "損益")
        self.assertEqual(pnl_field["value"], "損益: +0.42% / +1,200円(100株)")

    @patch.object(ShadowDiscordNotifier, "_post_embed", return_value=True)
    def test_notify_paper_exit_without_yen_still_sends(self, mock_post) -> None:
        notifier = ShadowDiscordNotifier(
            ShadowDiscordConfig(
                enabled=True,
                shadow_notify=True,
                paper_trade_notify=True,
                dedupe=False,
                cooldown_sec=0.0,
            )
        )
        notifier._webhook_url = "http://example.invalid/webhook"
        ok = notifier.notify_paper_exit(
            symbol="3905.T",
            entry_price=0.0,
            exit_price=0.0,
            entry_time=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc),
            exit_reason="hard_stop",
            pnl_pct=-0.5,
            mfe_pct=None,
            elapsed_min=5.0,
            pnl_yen_100=None,
        )
        self.assertTrue(ok)
        fields = mock_post.call_args.kwargs["fields"]
        pnl_field = next(f for f in fields if f["name"] == "損益")
        self.assertEqual(pnl_field["value"], "損益: -0.50%")


if __name__ == "__main__":
    unittest.main()

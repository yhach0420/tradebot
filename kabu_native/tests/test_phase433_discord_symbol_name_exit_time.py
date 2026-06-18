"""Phase433: Discord ENTRY/EXIT symbol name and EXIT time display."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from small_paper.discord_message_builder import (
    build_entry_detail,
    build_exit_detail,
    format_time_hms_jst,
)
from small_paper.discord_symbol_names import format_symbol_display


class TestPhase433DiscordSymbolNameExitTime(unittest.TestCase):
    def test_format_symbol_display_with_name(self) -> None:
        self.assertEqual(
            format_symbol_display("6976.T", "太陽誘電"),
            "6976.T 太陽誘電",
        )

    def test_format_symbol_display_fallback_code_only(self) -> None:
        self.assertEqual(
            format_symbol_display("9999.T", name_map={}),
            "9999.T",
        )

    def test_format_symbol_display_from_name_map(self) -> None:
        name_map = {"6976.T": "太陽誘電"}
        self.assertEqual(format_symbol_display("6976", name_map=name_map), "6976.T 太陽誘電")

    def test_format_time_hms_jst(self) -> None:
        self.assertEqual(
            format_time_hms_jst("2026-06-17T09:28:41+09:00"),
            "09:28:41",
        )

    def test_build_entry_detail_includes_symbol_name_and_time(self) -> None:
        detail = build_entry_detail(
            symbol="6976.T",
            entry_price=19955.0,
            stop_price=19700.0,
            slot_usage="2/5",
            entry_score_v2=4,
            data={},
            name_map={"6976.T": "太陽誘電"},
            entry_time="2026-06-17T09:12:34+09:00",
        )
        self.assertIn("銘柄: 6976.T 太陽誘電", detail)
        self.assertIn("時刻: 09:12:34", detail)

    def test_build_exit_detail_includes_symbol_name_exit_time_and_yen(self) -> None:
        detail = build_exit_detail(
            symbol="6976.T",
            entry_price=19900.0,
            exit_price=20070.0,
            pnl_pct=0.85,
            mfe_pct=1.2,
            mae_pct=-0.3,
            hold_minutes=16.0,
            exit_reason="trailing_mfe_exit",
            pnl_yen_100=8500.0,
            exit_time="2026-06-17T09:28:41+09:00",
            name_map={"6976.T": "太陽誘電"},
        )
        self.assertIn("銘柄: 6976.T 太陽誘電", detail)
        self.assertIn("EXIT時刻: 09:28:41", detail)
        self.assertIn("損益: +0.85% / +8,500円(100株)", detail)
        self.assertIn("EXIT理由: 利益確定条件到達", detail)

    @patch("small_paper.discord_notifier.get_cached_symbol_name_map")
    def test_notify_entry_title_uses_display(self, mock_map) -> None:
        from small_paper.discord_notifier import SmallPaperDiscordNotifier, SmallPaperDiscordConfig

        mock_map.return_value = {"6976.T": "太陽誘電"}
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        posted: list[dict] = []

        def _capture(**kwargs):
            posted.append(kwargs)
            return True

        notifier._post = _capture  # type: ignore[method-assign]
        notifier.notify_entry(
            event={
                "symbol": "6976.T",
                "event_time": "2026-06-17T09:12:34+09:00",
                "current_price": 19955.0,
                "entry_expectancy_score_v2": 4,
            },
            payload={},
            open_slots=2,
            session_bucket="morning",
        )
        self.assertEqual(posted[0]["title_line"], "【ENTRY】 6976.T 太陽誘電")
        self.assertEqual(posted[0]["fields"][2]["value"], "09:12:34")

    @patch("small_paper.discord_notifier.get_cached_symbol_name_map")
    def test_notify_exit_title_and_detail(self, mock_map) -> None:
        from small_paper.discord_notifier import SmallPaperDiscordNotifier, SmallPaperDiscordConfig

        mock_map.return_value = {"6976.T": "太陽誘電"}
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        posted: list[dict] = []

        def _capture(**kwargs):
            posted.append(kwargs)
            return True

        notifier._post = _capture  # type: ignore[method-assign]
        notifier.notify_exit(
            context={
                "symbol": "6976.T",
                "is_structural_exit": True,
                "exit_reason": "trailing_mfe_exit",
                "entry_price": 19900.0,
                "current_price": 20070.0,
                "realized_pnl_pct": 0.85,
                "pnl_yen_100": 8500.0,
                "hold_sec": 960.0,
                "exit_time": "2026-06-17T09:28:41+09:00",
            }
        )
        self.assertEqual(posted[0]["title_line"], "【EXIT】 6976.T 太陽誘電")
        detail = posted[0]["fields"][1]["value"]
        self.assertIn("EXIT時刻: 09:28:41", detail)
        self.assertIn("6976.T 太陽誘電", detail)


if __name__ == "__main__":
    unittest.main()

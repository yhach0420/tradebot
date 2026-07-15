"""Phase687W25C — Discord embed readability (aligned with R3 labels)."""

from __future__ import annotations

import unittest

from small_paper.discord_message_builder import (
    COLOR_ENTRY,
    COLOR_EXIT,
    PAPER_ONLY_FOOTER,
    TEST_FOOTER,
    build_cap_blocked_embed_payload,
    build_entry_embed_payload,
    build_exit_embed_payload,
    build_shadow_observation_embed_payload,
    build_summary_embed_payload,
    embed_to_discord_payload,
    exit_embed_color,
)


NAME_MAP = {"4174.T": "アピリッツ", "9983.T": "ファーストリテイリング"}


class Phase687W25CDiscordReadability(unittest.TestCase):
    def test_entry_embed_compact(self) -> None:
        emb = build_entry_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            slot_usage="3/5",
            entry_score_v2=3,
            data={
                "entry_route": "PBv2",
                "entry_reason_tokens": ["Momentum:low", "Board mid以上"],
            },
            name_map=NAME_MAP,
            entry_time="2026-07-14T10:06:53+09:00",
            stop_price=913.9,
        )
        self.assertEqual(emb["title"], "【ENTRY】4174.T アピリッツ")
        self.assertEqual(emb["color"], COLOR_ENTRY)
        self.assertIn("エントリー時間: 10:06:53", emb["description"])
        self.assertIn("ENTRY価格: 925円", emb["description"])
        self.assertIn("ENTRY方式: PBv2", emb["description"])
        self.assertIn("保有枠:", emb["description"])
        self.assertEqual(emb["footer"], PAPER_ONLY_FOOTER)

    def test_exit_colors_unified_orange(self) -> None:
        self.assertEqual(exit_embed_color("stop_hit"), COLOR_EXIT)
        self.assertEqual(exit_embed_color("trailing_mfe_exit"), COLOR_EXIT)

    def test_exit_embed_times_and_stale(self) -> None:
        emb = build_exit_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=15.1167,
            exit_reason="no_progress_exit",
            pnl_yen_100=0.0,
            name_map=NAME_MAP,
            entry_time="2026-07-14T10:06:53+09:00",
            exit_time="2026-07-14T10:21:59+09:00",
            market_time_age_sec=2070.0,
            stale_trade=True,
        )
        self.assertEqual(emb["color"], COLOR_EXIT)
        self.assertIn("エントリー時間: 10:06:53", emb["description"])
        self.assertIn("EXIT時間: 10:21:59", emb["description"])
        warn = next(f["value"] for f in emb["fields"] if f["name"] == "警告")
        self.assertIn("価格更新なし", warn)

    def test_cap_blocked_embed(self) -> None:
        emb = build_cap_blocked_embed_payload(
            symbol="4174.T",
            entry_score_v2=4,
            data={"entry_route": "PBv2"},
            active_positions=5,
            position_cap=5,
            name_map=NAME_MAP,
        )
        self.assertIn("保有: 5 / 5", emb["description"])

    def test_summary_embed(self) -> None:
        emb = build_summary_embed_payload(
            {
                "trade_count": 52,
                "win_count": 22,
                "loss_count": 22,
                "draw_count": 8,
                "total_pnl_yen_100": 21100,
                "profit_factor_yen_100": 1.34,
                "stop_count": 8,
                "max_concurrent": 5,
                "max_concurrent_cap": 5,
            },
            am_pm="AM",
            day_realized_pnl_yen_100=21100,
        )
        self.assertIn("取引数: 52", emb["description"])

    def test_shadow_heading(self) -> None:
        emb = build_shadow_observation_embed_payload(
            {"shadow_name": "rise5", "blocks": 3, "delta_yen": -1000},
            am_pm="AM",
        )
        self.assertIn("SHADOW OBSERVATION", emb["title"])

    def test_test_mode_labels(self) -> None:
        emb = build_exit_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=1.0,
            exit_reason="stop_hit",
            name_map=NAME_MAP,
            entry_time="2026-07-14T10:00:00+09:00",
            exit_time="2026-07-14T10:01:00+09:00",
            test_mode=True,
        )
        self.assertTrue(emb["title"].startswith("【TEST】【EXIT】"))
        self.assertEqual(emb["footer"], TEST_FOOTER)


if __name__ == "__main__":
    unittest.main()

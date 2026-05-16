"""Discord embed プレビュー用ヘルパーの軽量テスト（ネットワークなし）。"""

from __future__ import annotations

import unittest

from yahoo_kabu_watch import (
    Quote,
    _discord_embed_preview_prepend_level_emphasis,
    build_embed_paper_trade_breakout_minimal,
)


class TestDiscordEmbedPreviewEmphasis(unittest.TestCase):
    def test_prepend_inserts_banner(self) -> None:
        embed: dict = {"fields": [{"name": "現在値", "value": "100", "inline": True}]}
        _discord_embed_preview_prepend_level_emphasis(embed, entry=5000.0, stop=4900.0, take=5200.0)
        fs = embed.get("fields") or []
        self.assertGreaterEqual(len(fs), 2)
        self.assertIn("プレビュー強調", str(fs[0].get("name") or ""))
        self.assertIn("Entry", str(fs[0].get("value") or ""))


class TestBreakoutMinimalEmbed(unittest.TestCase):
    def test_minimal_title_and_levels_block(self) -> None:
        q = Quote(
            symbol="7011.T",
            price=4500.0,
            currency="JPY",
            previous_close=4300.0,
            change_percent=2.0,
            day_high=4700.0,
            day_low=4200.0,
            volume=1_000_000.0,
            market_time_utc=None,
            market_cap=None,
        )
        em = build_embed_paper_trade_breakout_minimal(
            q,
            entry=4423.0,
            stop=4335.0,
            take=4600.0,
            paper_live_times=("2026-05-13 10:00:01", "2026-05-13 10:00:02", "1.2 秒"),
            paper_trade_tier="Tier2（重点監視）",
        )
        self.assertIn("7011.T", str(em.get("title") or ""))
        self.assertIn("Entry上抜け", str(em.get("title") or ""))
        blob = "\n".join(
            f"{str(f.get('name') or '')}|{str(f.get('value') or '')}" for f in (em.get("fields") or [])
        )
        self.assertIn("Entry", blob)
        self.assertIn("Stop", blob)
        self.assertIn("Take", blob)
        self.assertIn("検出時刻", blob)
        self.assertIn("遅延", blob)
        self.assertIn("Tier2", blob)


if __name__ == "__main__":
    unittest.main()

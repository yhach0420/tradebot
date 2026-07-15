"""Phase687W25C-R2 — legacy ENTRY/EXIT embed + time fields (updated for R3 labels)."""

from __future__ import annotations

import unittest

from small_paper.discord_message_builder import (
    COLOR_ENTRY,
    COLOR_EXIT,
    PAPER_ONLY_FOOTER,
    audit_discord_shadow_inventory,
    build_entry_embed_payload,
    build_exit_embed_payload,
    build_shadow_observation_embed_payload,
    collect_active_shadow_observations,
    exit_embed_color,
    format_shadow_summary_lines,
)


NAME_MAP = {"4174.T": "アピリッツ"}


class Phase687W25CR2LegacyEmbed(unittest.TestCase):
    def test_entry_legacy_body_and_time(self) -> None:
        emb = build_entry_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            slot_usage="3/5",
            entry_score_v2=3,
            data={
                "price_age_sec": 0.8,
                "board_age_sec": 0.2,
                "price_source": "event_fresh",
                "entry_reason_tokens": ["Momentum:low", "Board mid以上"],
            },
            name_map=NAME_MAP,
            entry_time="2026-07-14T10:06:53+09:00",
            stop_price=913.9,
        )
        self.assertEqual(emb["color"], COLOR_ENTRY)
        self.assertIn("エントリー時間: 10:06:53", emb["description"])
        self.assertIn("ENTRY価格:", emb["description"])
        self.assertIn("保有枠: 3/5", emb["description"])
        self.assertIn("ENTRY方式:", emb["description"])
        self.assertIn("ENTRY理由", [f["name"] for f in emb["fields"]])
        self.assertEqual(emb["footer"], PAPER_ONLY_FOOTER)

    def test_exit_all_orange_with_times(self) -> None:
        for reason in ("stop_hit", "trailing_mfe_exit", "no_progress_exit", "session_close"):
            emb = build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=920.0,
                pnl_pct=-0.54,
                mfe_pct=0.4,
                mae_pct=-0.5,
                hold_minutes=15.1167,
                exit_reason=reason,
                pnl_yen_100=-500.0,
                name_map=NAME_MAP,
                entry_time="2026-07-14T10:06:53+09:00",
                exit_time="2026-07-14T10:21:59+09:00",
            )
            self.assertEqual(emb["color"], COLOR_EXIT, reason)
            self.assertEqual(exit_embed_color(reason), COLOR_EXIT)
            self.assertIn("エントリー時間: 10:06:53", emb["description"])
            self.assertIn("EXIT時間: 10:21:59", emb["description"])

    def test_stale_exit_keeps_orange(self) -> None:
        emb = build_exit_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=15.0,
            exit_reason="no_progress_exit",
            pnl_yen_100=0.0,
            name_map=NAME_MAP,
            entry_time="2026-07-14T10:06:53+09:00",
            exit_time="2026-07-14T10:21:59+09:00",
            market_time_age_sec=2070.0,
            stale_trade=True,
            price_freshness_source="liquidity_stale_trade",
        )
        self.assertEqual(emb["color"], COLOR_EXIT)
        warn = next(f["value"] for f in emb["fields"] if f["name"] == "警告")
        self.assertIn("rejectではない", warn)

    def test_shadow_active_only(self) -> None:
        summary = {
            "pbv2_rise5_shadow_enabled": True,
            "pbv2_rise5_shadow_block_count": 2,
            "pbv2_rise5_shadow_net_effect_yen": -800,
            "pbv2_flat_band_shadow_enabled": True,
            "pbv2_flat_band_shadow_block_count": 0,
        }
        active = collect_active_shadow_observations(summary)
        self.assertEqual([a["name"] for a in active], ["Rise5"])
        emb = build_shadow_observation_embed_payload(
            {"shadow_name": "Rise5", "active_shadows": active},
            am_pm="AM",
        )
        self.assertTrue("SHADOW OBSERVATION" in emb["title"])
        self.assertIn("observation only", emb["description"])
        audit = audit_discord_shadow_inventory(summary)
        self.assertEqual(audit["verdict"], "SHADOW_INVENTORY_OK")


if __name__ == "__main__":
    unittest.main()

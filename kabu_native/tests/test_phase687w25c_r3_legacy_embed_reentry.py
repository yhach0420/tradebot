"""Phase687W25C-R3 — legacy embed + re-entry visibility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from small_paper.daily_symbol_discord_state import (
    DailySymbolDiscordState,
    reset_daily_symbol_state_for_tests,
    restore_state_from_events,
    trading_date_jst,
)
from small_paper.discord_message_builder import (
    COLOR_ENTRY,
    COLOR_EXIT,
    PAPER_ONLY_FOOTER,
    build_entry_embed_payload,
    build_exit_embed_payload,
    build_summary_embed_payload,
    collect_active_shadow_observations,
    exit_embed_color,
    write_shadow_inventory_csvs,
)


NAME = {"4174.T": "アピリッツ"}


class Phase687W25CR3(unittest.TestCase):
    def setUp(self) -> None:
        reset_daily_symbol_state_for_tests()

    def tearDown(self) -> None:
        reset_daily_symbol_state_for_tests()

    def test_entry_first_no_reentry_block(self) -> None:
        emb = build_entry_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            stop_price=913.9,
            slot_usage="3→4/5",
            entry_score_v2=3,
            data={"entry_reason_tokens": ["Momentum:low"]},
            name_map=NAME,
            entry_time="2026-07-14T10:38:44+09:00",
            reentry_info={"entry_count_today_after": 1, "is_reentry": False},
        )
        self.assertEqual(emb["color"], COLOR_ENTRY)
        self.assertIn("エントリー時間: 10:38:44", emb["description"])
        self.assertIn("ENTRY価格: 925円", emb["description"])
        self.assertIn("損切り価格:", emb["description"])
        self.assertIn("保有枠: 3→4/5", emb["description"])
        self.assertIn("ENTRY方式: PBv2", emb["description"])
        self.assertNotIn("本日同銘柄ENTRY", emb["description"])
        self.assertEqual(emb["footer"], PAPER_ONLY_FOOTER)

    def test_entry_reentry_block(self) -> None:
        emb = build_entry_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            stop_price=913.9,
            slot_usage="2→3/5",
            entry_score_v2=3,
            data={},
            name_map=NAME,
            entry_time="2026-07-14T10:38:44+09:00",
            reentry_info={
                "entry_count_today_after": 4,
                "is_reentry": True,
                "previous_exit_reason": "no_progress_exit",
                "previous_exit_reason_ja": "停滞ポジション整理",
                "previous_exit_time_hms": "10:37:13",
                "previous_exit_elapsed": "1分31秒",
                "previous_exit_price": 925.0,
            },
        )
        self.assertIn("本日同銘柄ENTRY: 4回目", emb["description"])
        self.assertIn("前回EXIT: 停滞ポジション整理", emb["description"])
        self.assertIn("前回EXIT時刻: 10:37:13", emb["description"])
        self.assertIn("前回EXITから: 1分31秒", emb["description"])
        self.assertIn("前回EXIT価格: 925円", emb["description"])

    def test_exit_orange_and_cum(self) -> None:
        for reason in ("stop_hit", "trailing_mfe_exit", "no_progress_exit", "session_close"):
            emb = build_exit_embed_payload(
                symbol="4174.T",
                entry_price=925.0,
                exit_price=925.0,
                pnl_pct=0.0,
                mfe_pct=0.0,
                mae_pct=0.0,
                hold_minutes=15.2,
                exit_reason=reason,
                pnl_yen_100=0.0,
                name_map=NAME,
                entry_time="2026-07-14T10:22:01+09:00",
                exit_time="2026-07-14T10:37:13+09:00",
                symbol_pnl_yen_100_today=-2300.0,
            )
            self.assertEqual(emb["color"], COLOR_EXIT)
            self.assertEqual(exit_embed_color(reason), COLOR_EXIT)
            self.assertIn("エントリー時間: 10:22:01", emb["description"])
            self.assertIn("EXIT時間: 10:37:13", emb["description"])
            self.assertIn("本日同銘柄累計: -2,300円", emb["description"])
            self.assertIn("EXIT理由:", emb["description"])
            self.assertIn("最大含み益 MFE:", emb["description"])

    def test_stale_warn_only_when_abnormal(self) -> None:
        ok = build_exit_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=1.0,
            exit_reason="no_progress_exit",
            name_map=NAME,
            entry_time="2026-07-14T10:00:00+09:00",
            exit_time="2026-07-14T10:01:00+09:00",
            price_age_sec=1.0,
        )
        self.assertEqual(ok["fields"], [])
        stale = build_exit_embed_payload(
            symbol="4174.T",
            entry_price=925.0,
            exit_price=925.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=1.0,
            exit_reason="no_progress_exit",
            name_map=NAME,
            entry_time="2026-07-14T10:00:00+09:00",
            exit_time="2026-07-14T10:01:00+09:00",
            market_time_age_sec=2070.0,
            stale_trade=True,
        )
        self.assertTrue(any(f["name"] == "警告" for f in stale["fields"]))

    def test_daily_state_am_pm_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = "20260714"
            st = DailySymbolDiscordState(
                trading_date=day,
                _path=root / "results" / "small_paper" / day / "daily_symbol_discord_state.json",
            )
            s1 = st.record_accepted_entry("4174.T", entry_time="2026-07-14T09:00:00+09:00")
            self.assertFalse(s1["is_reentry"])
            st.record_official_exit(
                "4174.T",
                exit_reason="no_progress_exit",
                exit_time="2026-07-14T09:15:00+09:00",
                exit_price=925.0,
                pnl_yen_100=-500.0,
            )
            s2 = st.record_accepted_entry("4174.T", entry_time="2026-07-14T09:16:31+09:00")
            self.assertTrue(s2["is_reentry"])
            self.assertEqual(s2["entry_count_today_after"], 2)
            self.assertIn("no_progress", s2["previous_exit_reason"])
            self.assertEqual(st.same_symbol_reentry_count_day, 1)
            self.assertEqual(st.reentry_after_no_progress_count_day, 1)
            st.record_same_push_suppression("4174.T")
            self.assertEqual(st.same_push_suppression_count_day, 1)
            st.save()

            # next day reset
            st.ensure_day("20260715")
            self.assertEqual(st.symbols, {})
            self.assertEqual(st.same_push_suppression_count_day, 0)

            # restore from events
            events = [
                {"event_type": "accepted", "symbol": "4174.T", "event_time": "2026-07-14T09:00:00+09:00"},
                {
                    "event_type": "observer_exit",
                    "symbol": "4174.T",
                    "exit_reason": "stop_hit",
                    "exit_time": "2026-07-14T09:10:00+09:00",
                    "entry_price": 1000,
                    "current_price": 990,
                    "pnl_yen_100": -1000,
                },
                {
                    "event_type": "rejected",
                    "symbol": "4174.T",
                    "gate_reject_reason": "same_push_reentry_after_no_progress_exit",
                    "same_push_reentry_skip": True,
                },
                {"event_type": "rejected", "symbol": "4174.T", "gate_reject_reason": "other"},
            ]
            restored = restore_state_from_events(events, trading_date=day, native_root=root)
            self.assertEqual(restored.get("4174.T").entry_count_today, 1)
            self.assertEqual(restored.same_push_suppression_count_day, 1)
            self.assertEqual(restored.get("4174.T").realized_pnl_yen_100_today, -1000.0)

    def test_summary_reentry_audit(self) -> None:
        emb = build_summary_embed_payload(
            {
                "trade_count": 31,
                "win_count": 19,
                "loss_count": 12,
                "draw_count": 0,
                "total_pnl_yen_100": 12000,
                "profit_factor_yen_100": 1.29,
                "stop_count": 4,
                "max_concurrent": 5,
                "max_concurrent_cap": 5,
            },
            am_pm="PM",
            day_realized_pnl_yen_100=33100,
            reentry_audit={
                "same_symbol_reentry_count": 6,
                "reentry_after_no_progress_count": 2,
                "same_push_suppression_count": 3,
            },
        )
        self.assertIn("PM損益:", emb["description"])
        self.assertIn("本日累計: +33,100円", emb["description"])
        audit = next(f["value"] for f in emb["fields"] if f["name"] == "再ENTRY監査")
        self.assertIn("同一銘柄再ENTRY: 6件", audit)
        self.assertIn("same-PUSH抑止: 3件", audit)

    def test_shadow_filter_and_csv(self) -> None:
        summary = {
            "pbv2_rise5_shadow_enabled": True,
            "pbv2_rise5_shadow_block_count": 2,
            "pbv2_rise5_shadow_net_effect_yen": -800,
            "pbv2_flat_band_shadow_enabled": True,
            "pbv2_flat_band_shadow_block_count": 0,
        }
        active = collect_active_shadow_observations(summary)
        self.assertEqual([a["name"] for a in active], ["Rise5"])
        with tempfile.TemporaryDirectory() as td:
            paths = write_shadow_inventory_csvs(summary, out_dir=td)
            self.assertTrue(Path(paths["displayed_path"]).is_file())
            self.assertTrue(Path(paths["hidden_path"]).is_file())
            hidden = Path(paths["hidden_path"]).read_text(encoding="utf-8")
            self.assertIn("Flat-band", hidden)


if __name__ == "__main__":
    unittest.main()

"""Phase637: Discord operator status sections (no trading logic)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades
from small_paper.discord_message_builder import (
    build_operator_status_embed_fields,
    format_discord_summary_lines,
    format_research_shadow_daily_summary_lines,
    format_todays_insight_lines,
)
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier


def _exit_event(
    *,
    symbol: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    mfe_pct: float,
    mae_pct: float,
    hold_minutes: float,
    exit_reason: str,
    pnl_yen_100: float,
    entry_type: str = "PBV2",
) -> dict:
    return {
        "event_type": "observer_exit",
        "symbol": symbol,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "hold_minutes": hold_minutes,
        "exit_reason": exit_reason,
        "pnl_yen_100": pnl_yen_100,
        "entry_type": entry_type,
        "stop_hit": exit_reason == "stop_hit",
    }


class Phase637DiscordNotificationTests(unittest.TestCase):
    def _sample_events(self) -> list[dict]:
        return [
            _exit_event(
                symbol="6976.T",
                entry_price=1000.0,
                exit_price=1010.0,
                pnl_pct=1.0,
                mfe_pct=1.5,
                mae_pct=-0.2,
                hold_minutes=12.0,
                exit_reason="trailing_mfe_exit",
                pnl_yen_100=1000.0,
            ),
            _exit_event(
                symbol="4062.T",
                entry_price=1000.0,
                exit_price=990.0,
                pnl_pct=-1.0,
                mfe_pct=0.2,
                mae_pct=-1.0,
                hold_minutes=8.0,
                exit_reason="stop_hit",
                pnl_yen_100=-1000.0,
            ),
        ]

    def _sample_summary(self, events: list[dict]) -> dict:
        canonical = build_canonical_summary(
            collect_canonical_trades(events),
            peak_open_slots=2,
            max_concurrent_positions=3,
        )
        return {
            "canonical_summary": canonical,
            "pbv2_count": 1,
            "or_count": 1,
            "accepted_count": 2,
            "observer_exit_count_with_pnl": 2,
            "pbv2_rise5_shadow_enabled": True,
            "pbv2_rise5_shadow_threshold_pct": 1.84,
            "pbv2_rise5_shadow_block_count": 3,
            "pbv2_rise5_shadow_kept_count": 10,
            "pbv2_rise5_shadow_target_count": 13,
            "pbv2_rise5_shadow_blocked_winners": 1,
            "pbv2_rise5_shadow_blocked_losers": 2,
            "pbv2_rise5_shadow_blocked_pnl_yen_100": -500.0,
            "pbv2_rise5_shadow_net_effect_yen": 500.0,
            "freshness_semantics_v2_enabled": True,
            "event_stale_reject_count": 0,
            "board_stale_reject_count": 4,
            "trade_stale_tag_count": 12,
            "entry_cluster_guard_enabled": True,
            "cluster_guard_reject_count": 2,
            "cluster_guard_exception_count": 0,
            "cluster_guard_exception_pnl": 0.0,
            "cluster_guard_exception_pf": 0.0,
            "gate_dominance_alert_level": "none",
            "gate_dominance_top_reason": "high_drift_pullback",
            "gate_dominance_top_share_pct": 24.5,
            "gate_dominance_total_rejects": 100,
            "entry_quality_guard_enabled": True,
            "entry_quality_guard_reject_count": 5,
            "entry_quality_guard_spread_reject_count": 1,
            "entry_quality_guard_update_reject_count": 4,
            "exit_shadow_monitor_enabled": True,
            "exit_mfe_capture_ratio": 0.9,
            "exit_opportunity_loss_avg": 0.1,
            "exit_early_profit_take_count": 0,
            "shadow_exit_t3_delta": 0.0,
            "shadow_exit_t2_delta": 0.0,
            "pullback_misread_guard_shadow_enabled": True,
            "pullback_misread_guard_shadow_delta_yen": 1000.0,
            "pullback_misread_guard_shadow_blocked_count": 2,
            "board_dynamic_shadow_enabled": True,
            "board_dynamic_shadow_total_delta_yen": 200.0,
            "peak_open_slots": 2,
            "max_concurrent_positions": 3,
            "api_error_count": 0,
            "stale_tick_count": 1,
            "data_gap_count": 0,
            "live_feature_complete_rate_pct": 99.5,
            "config_sha256": "abcd1234efgh5678",
        }

    def test_operator_status_sections_present(self) -> None:
        events = self._sample_events()
        summary = self._sample_summary(events)
        fields = build_operator_status_embed_fields(events=events, summary=summary)
        names = [f["name"] for f in fields]
        self.assertEqual(
            names,
            [
                "PBv2 Summary",
                "Rise5 Shadow Summary",
                "Freshness Summary",
                "Cluster Guard Summary",
                "Gate Dominance Summary",
                "ENTRY Quality Summary",
                "EXIT Summary",
                "Shadow Summary",
                "Today's Insight",
                "System Health",
            ],
        )

    def test_operator_sections_use_measured_values(self) -> None:
        events = self._sample_events()
        summary = self._sample_summary(events)
        fields = {f["name"]: f["value"] for f in build_operator_status_embed_fields(events=events, summary=summary)}
        self.assertIn("PBv2 accepted: 1", fields["PBv2 Summary"])
        self.assertIn("OR accepted: 1", fields["PBv2 Summary"])
        self.assertIn("net_effect:", fields["Rise5 Shadow Summary"])
        self.assertIn("board_stale rejects: 4", fields["Freshness Summary"])
        self.assertIn("reject: 2", fields["Cluster Guard Summary"])
        self.assertIn("high_drift_pullback", fields["Gate Dominance Summary"])
        self.assertIn("reject total: 5", fields["ENTRY Quality Summary"])
        self.assertIn("Rise5:", fields["Shadow Summary"])
        self.assertIn("api_errors: 0", fields["System Health"])

    def test_insight_is_measured_only(self) -> None:
        events = self._sample_events()
        summary = self._sample_summary(events)
        lines = format_todays_insight_lines(summary, events)
        self.assertTrue(any("Rise5 shadow block=3" in line for line in lines))
        self.assertTrue(any("EntryQuality reject=5" in line for line in lines))
        self.assertTrue(any("ClusterGuard reject=2" in line for line in lines))
        joined = "\n".join(lines)
        self.assertNotIn("おそらく", joined)
        self.assertNotIn("はず", joined)

    def test_insight_no_alert_when_quiet(self) -> None:
        lines = format_todays_insight_lines(
            {
                "accepted_count": 1,
                "observer_exit_count_with_pnl": 1,
                "gate_dominance_alert_level": "none",
            },
            self._sample_events(),
        )
        self.assertEqual(lines, ["主要アラートなし"])

    def test_canonical_detail_unchanged(self) -> None:
        events = self._sample_events()
        canonical = build_canonical_summary(
            collect_canonical_trades(events),
            peak_open_slots=2,
            max_concurrent_positions=3,
        )
        detail = "\n".join(format_discord_summary_lines(canonical))
        self.assertIn("trade_count:", detail)
        self.assertNotIn("PBv2 Summary", detail)
        self.assertNotIn("Today's Insight", detail)

    def test_research_shadow_omits_operator_covered(self) -> None:
        summary = {
            "entry_quality_guard_enabled": True,
            "entry_quality_guard_reject_count": 1,
            "entry_quality_guard_spread_reject_count": 0,
            "entry_quality_guard_update_reject_count": 1,
            "entry_cluster_guard_enabled": True,
            "cluster_guard_reject_count": 1,
            "cluster_guard_exception_count": 0,
            "cluster_guard_exception_pnl": 0,
            "cluster_guard_exception_pf": 0,
            "pbv2_rise5_shadow_enabled": True,
            "pbv2_rise5_shadow_block_count": 1,
            "pbv2_rise5_shadow_blocked_pnl_yen_100": 0,
            "pbv2_rise5_shadow_net_effect_yen": 0,
            "exit_shadow_monitor_enabled": True,
            "exit_mfe_capture_ratio": 0.9,
            "exit_opportunity_loss_avg": 0.1,
            "exit_early_profit_take_count": 0,
            "high_drift_pullback_guard_enabled": True,
            "high_drift_pullback_reject_count": 3,
        }
        full = format_research_shadow_daily_summary_lines(summary)
        compact = format_research_shadow_daily_summary_lines(summary, omit_operator_covered=True)
        self.assertTrue(any("EntryQuality" in line for line in full))
        self.assertTrue(any("ClusterGuard" in line for line in full))
        self.assertTrue(any("PBv2 Rise5 Shadow" in line for line in full))
        self.assertFalse(any("EntryQuality" in line for line in compact))
        self.assertFalse(any("ClusterGuard" in line for line in compact))
        self.assertFalse(any("PBv2 Rise5 Shadow" in line for line in compact))
        self.assertTrue(any("HighDriftPullback" in line for line in compact))

    @patch("small_paper.discord_notifier.get_cached_symbol_name_map")
    def test_production_summary_includes_operator_sections(self, mock_map) -> None:
        mock_map.return_value = {"6976.T": "太陽誘電"}
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True, send_daily_summary=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        events = self._sample_events()
        summary = self._sample_summary(events)
        fields = notifier._production_summary_fields(events=events, summary=summary)
        assert fields is not None
        names = [f["name"] for f in fields]
        self.assertIn("詳細", names)
        self.assertIn("PBv2 Summary", names)
        self.assertIn("Rise5 Shadow Summary", names)
        self.assertIn("Today's Insight", names)
        self.assertIn("System Health", names)
        # Phase490 observability blocks are replaced by Phase637 operator sections.
        self.assertNotIn("Symbol Attribution", names)
        self.assertNotIn("Runtime Health", names)


if __name__ == "__main__":
    unittest.main()

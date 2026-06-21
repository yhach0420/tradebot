"""Phase490: Discord observability blocks (C01/C02/C03/C05/C06)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades
from small_paper.discord_message_builder import (
    build_daily_summary_detail,
    build_exit_detail,
    build_observability_embed_fields,
    count_stop_low_mfe,
    format_discord_summary_lines,
    format_exit_breakdown_lines,
    format_heartbeat_runtime_health_fields,
    format_reject_funnel_lines,
    format_runtime_health_lines,
    format_symbol_attribution_lines,
    is_stop_low_mfe_exit,
)
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier


def _exit(
    symbol: str,
    entry: float,
    exit_p: float,
    pnl_pct: float,
    *,
    exit_reason: str = "trailing_mfe_exit",
    mfe_pct: float | None = 1.2,
    **extra: object,
) -> dict:
    return {
        "event_type": "observer_exit",
        "symbol": symbol,
        "entry_price": entry,
        "exit_price": exit_p,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
        "mfe_pct": mfe_pct,
        **extra,
    }


class TestPhase490ObservabilityUpgrade(unittest.TestCase):
    def _sample_events(self) -> list[dict]:
        return [
            _exit("6976.T", 20000, 20100, 0.5, exit_reason="trailing_mfe_exit", mfe_pct=1.0),
            _exit("6976.T", 20000, 19900, -0.5, exit_reason="stop_hit", mfe_pct=0.3),
            _exit("4062.T", 1000, 990, -1.0, exit_reason="stop_hit", mfe_pct=0.2),
            _exit("7203.T", 2500, 2525, 1.0, exit_reason="no_progress_exit", mfe_pct=0.4),
            _exit("9984.T", 3000, 3005, 0.17, exit_reason="afternoon_session_close", mfe_pct=0.2),
        ]

    def test_symbol_attribution_lines(self) -> None:
        lines = format_symbol_attribution_lines(self._sample_events())
        joined = "\n".join(lines)
        self.assertIn("6976", joined)
        self.assertIn("top3_share:", joined)
        self.assertRegex(joined, r"\d+T")

    def test_exit_breakdown_includes_stop_low_mfe(self) -> None:
        lines = format_exit_breakdown_lines(self._sample_events())
        joined = "\n".join(lines)
        self.assertIn("stop_hit:", joined)
        self.assertIn("no_progress:", joined)
        self.assertIn("session_close:", joined)
        self.assertIn("stop_low_mfe:", joined)
        self.assertEqual(count_stop_low_mfe(self._sample_events()), 2)

    def test_runtime_health_and_reject_funnel_lines(self) -> None:
        summary = {
            "api_error_count": 1,
            "stale_tick_count": 3089,
            "data_gap_count": 38,
            "live_feature_complete_rate_pct": 94.82,
            "config_sha256": "15113c9dabc3c45",
            "peak_open_slots": 5,
            "max_concurrent_positions": 5,
            "reject_reason_counts": {
                "high_drift_pullback": 4385,
                "data_stale_price": 31901,
                "late_chase_guard": 12,
                "max_concurrent": 1658,
            },
        }
        health = "\n".join(format_runtime_health_lines(summary))
        self.assertIn("stale_ticks: 3089", health)
        self.assertIn("feature_complete: 94.8%", health)
        self.assertIn("config: …3c45", health)
        self.assertIn("peak_slots: 5/5", health)
        funnel = "\n".join(format_reject_funnel_lines(summary))
        self.assertIn("data_stale_price: 31901", funnel)
        self.assertIn("high_drift_pullback: 4385", funnel)

    def test_build_exit_detail_stop_low_mfe_tag(self) -> None:
        detail = build_exit_detail(
            symbol="4062.T",
            entry_price=1000.0,
            exit_price=990.0,
            pnl_pct=-1.0,
            mfe_pct=0.2,
            mae_pct=-1.0,
            hold_minutes=8.0,
            exit_reason="stop_hit",
            pnl_yen_100=-1000.0,
        )
        self.assertIn("stop_low_mfe", detail)
        self.assertTrue(is_stop_low_mfe_exit("stop_hit", 0.2))
        self.assertFalse(is_stop_low_mfe_exit("trailing_mfe_exit", 1.2))

    def test_build_observability_embed_fields(self) -> None:
        summary = {
            "peak_open_slots": 3,
            "max_concurrent_positions": 5,
            "reject_reason_counts": {"max_concurrent": 10},
        }
        fields = build_observability_embed_fields(
            events=self._sample_events(),
            summary=summary,
        )
        names = [f["name"] for f in fields]
        self.assertEqual(
            names,
            ["Symbol Attribution", "Exit Breakdown", "Runtime Health", "Reject Funnel"],
        )

    def test_canonical_summary_lines_unchanged(self) -> None:
        events = self._sample_events()
        canonical = build_canonical_summary(
            collect_canonical_trades(events),
            peak_open_slots=3,
            max_concurrent_positions=5,
        )
        detail = build_daily_summary_detail(canonical)
        self.assertEqual(detail, "\n".join(format_discord_summary_lines(canonical)))
        self.assertNotIn("Symbol Attribution", detail)
        self.assertNotIn("stop_low_mfe", detail)

    @patch("small_paper.discord_notifier.get_cached_symbol_name_map")
    def test_production_summary_fields_include_observability(self, mock_map) -> None:
        mock_map.return_value = {"6976.T": "太陽誘電"}
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True, send_daily_summary=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        events = self._sample_events()
        canonical = build_canonical_summary(
            collect_canonical_trades(events),
            peak_open_slots=3,
            max_concurrent_positions=5,
        )
        summary = {
            "canonical_summary": canonical,
            "peak_open_slots": 3,
            "max_concurrent_positions": 5,
            "reject_reason_counts": {"late_chase_guard": 12},
        }
        fields = notifier._production_summary_fields(events=events, summary=summary)
        assert fields is not None
        names = [f["name"] for f in fields]
        self.assertIn("詳細", names)
        self.assertIn("Symbol Attribution", names)
        self.assertIn("Exit Breakdown", names)
        self.assertIn("Runtime Health", names)
        self.assertIn("Reject Funnel", names)

    @patch("small_paper.discord_notifier.get_cached_symbol_name_map")
    def test_notify_exit_includes_stop_low_mfe(self, mock_map) -> None:
        mock_map.return_value = {}
        cfg = SmallPaperDiscordConfig(enabled=True, observer_only=True)
        notifier = SmallPaperDiscordNotifier(cfg, profile="p", entry_profile="e")
        posted: list[dict] = []

        def _capture(**kwargs):
            posted.append(kwargs)
            return True

        notifier._post = _capture  # type: ignore[method-assign]
        notifier.notify_exit(
            context={
                "symbol": "4062.T",
                "is_structural_exit": True,
                "exit_reason": "stop_hit",
                "entry_price": 1000.0,
                "current_price": 990.0,
                "realized_pnl_pct": -1.0,
                "pnl_yen_100": -1000.0,
                "mfe_pct": 0.2,
                "hold_sec": 480.0,
            }
        )
        detail = posted[0]["fields"][1]["value"]
        self.assertIn("stop_low_mfe", detail)

    def test_heartbeat_runtime_health_fields(self) -> None:
        fields = format_heartbeat_runtime_health_fields(
            {
                "data_gap_count": 38,
                "live_feature_complete_rate_pct": 94.8,
                "config_sha256": "15113c9dabc3c45",
                "peak_open_slots": 5,
                "max_concurrent_positions": 5,
            }
        )
        by_name = {f["name"]: f["value"] for f in fields}
        self.assertEqual(by_name["data_gaps"], "38")
        self.assertEqual(by_name["feature_complete"], "94.8%")
        self.assertEqual(by_name["config"], "…3c45")
        self.assertEqual(by_name["peak_slots"], "5/5")


if __name__ == "__main__":
    unittest.main()

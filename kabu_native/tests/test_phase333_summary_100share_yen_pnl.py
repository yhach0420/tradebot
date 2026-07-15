import unittest

from replay.pnl_yen import (
    format_summary_avg_pnl_yen_100,
    format_summary_profit_factor_yen,
    format_summary_total_pnl_line,
)
from small_paper.discord_message_builder import (
    aggregate_daily_metrics,
    build_daily_summary_detail,
    format_discord_summary_lines,
    format_summary_yen_display_lines,
    summarize_observer_exit_metrics,
    summary_notification_labels,
)


class TestPhase333Summary100ShareYenPnl(unittest.TestCase):
    def _sample_events(self) -> list[dict]:
        return [
            {
                "event_type": "observer_exit",
                "symbol": "6981.T",
                "entry_price": 1000.0,
                "exit_price": 990.0,
                "pnl_pct": -1.0,
            },
            {
                "event_type": "observer_exit",
                "symbol": "7220.T",
                "entry_price": 2000.0,
                "exit_price": 1986.0,
                "pnl_pct": -0.7,
            },
        ]

    def test_summarize_observer_exit_metrics(self) -> None:
        metrics = summarize_observer_exit_metrics(self._sample_events())
        self.assertEqual(metrics["total_pnl_yen_100"], -2400.0)
        self.assertEqual(metrics["avg_pnl_yen_100"], -1200.0)
        self.assertAlmostEqual(metrics["total_pnl_pct"], -1.7, places=4)
        self.assertEqual(metrics["profit_factor_yen_100"], 0.0)

    def test_format_summary_display_lines_with_yen(self) -> None:
        metrics = {
            "total_pnl_pct": -1.7,
            "total_pnl_yen_100": -47400.0,
            "avg_pnl_yen_100": -280.0,
            "profit_factor": 0.863,
        }
        lines = format_summary_yen_display_lines(metrics)
        self.assertEqual(lines[0], "最終損益: -1.70% / -47,400円(100株)")
        self.assertEqual(lines[1], "平均損益: -280円/取引(100株)")
        self.assertEqual(lines[2], "PF: 0.863")

    def test_format_summary_without_yen_still_works(self) -> None:
        lines = format_summary_yen_display_lines({"total_pnl_pct": 0.5})
        self.assertEqual(lines[0], "最終損益: +0.50%")
        self.assertEqual(len(lines), 1)

    def test_aggregate_daily_metrics_unifies_profit_factor(self) -> None:
        events = self._sample_events()
        metrics = aggregate_daily_metrics(
            events,
            {"peak_open_slots": 2, "observer_entry_count": 2, "observer_exit_count": 2},
            max_concurrent_positions=3,
        )
        self.assertEqual(metrics["profit_factor"], metrics["profit_factor_yen_100"])

    def test_discord_summary_actual_only_fields(self) -> None:
        from small_paper.canonical_summary import build_canonical_summary, collect_canonical_trades

        events = self._sample_events()
        canonical = build_canonical_summary(
            collect_canonical_trades(events),
            peak_open_slots=2,
            max_concurrent_positions=3,
            watch_symbols_count=50,
        )
        detail = build_daily_summary_detail(canonical)
        lines = format_discord_summary_lines(canonical)
        self.assertEqual(detail, "\n".join(lines))
        self.assertIn("取引数: 2", detail)
        self.assertIn("PF:", detail)
        self.assertIn("勝率:", detail)
        self.assertIn("最終損益:", detail)
        self.assertIn("監視銘柄数: 50", detail)
        self.assertIn("取引銘柄数: 2", detail)
        self.assertNotIn("total_pnl_pct", detail)
        self.assertNotIn("avg_pnl_pct", detail)
        self.assertNotIn("shadow", detail.lower())
        self.assertNotIn("見送り最高score", detail)
        self.assertNotIn("score5", detail)
        self.assertEqual(detail.count("PF:"), 1)
        self.assertNotIn("profit_factor_yen_100:", detail)

    def test_aggregate_daily_metrics_uses_yen_win_rate(self) -> None:
        events = [
            {
                "event_type": "observer_exit",
                "symbol": "9984.T",
                "entry_price": 30000.0,
                "exit_price": 30300.0,
                "pnl_pct": 1.0,
            },
            {
                "event_type": "observer_exit",
                "symbol": "7203.T",
                "entry_price": 500.0,
                "exit_price": 490.0,
                "pnl_pct": -2.0,
            },
        ]
        metrics = aggregate_daily_metrics(
            events,
            {"peak_open_slots": 2},
            max_concurrent_positions=3,
        )
        self.assertGreater(metrics["total_pnl_yen_100"], 0)
        self.assertLess(metrics["total_pnl_pct"], 0)
        self.assertEqual(metrics["win_rate_yen_100"], metrics["win_rate"])
        self.assertEqual(metrics["profit_factor"], metrics["profit_factor_yen_100"])

    def test_summary_notification_labels_am_pm(self) -> None:
        self.assertEqual(
            summary_notification_labels({"am_pm_session": {"kind": "am"}}),
            ("AM Summary", "【AM Summary】"),
        )
        self.assertEqual(
            summary_notification_labels({"am_pm_session": {"kind": "pm"}}),
            ("PM Summary", "【PM Summary】"),
        )
        self.assertEqual(
            summary_notification_labels({}),
            ("Daily Summary", "【Daily Summary】"),
        )

    def test_pnl_yen_format_helpers(self) -> None:
        self.assertEqual(
            format_summary_total_pnl_line(-1.7, -47400.0),
            "最終損益: -1.70% / -47,400円(100株)",
        )
        self.assertEqual(format_summary_avg_pnl_yen_100(-280.0), "-280円/取引(100株)")
        self.assertEqual(format_summary_profit_factor_yen(0.863), "0.863")
        self.assertEqual(format_summary_profit_factor_yen("inf"), "inf")
        self.assertEqual(format_summary_profit_factor_yen(None), "—")


if __name__ == "__main__":
    unittest.main()

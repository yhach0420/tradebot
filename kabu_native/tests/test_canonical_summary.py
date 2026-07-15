"""Canonical summary / Discord Summary consistency (PhaseXXX)."""

from __future__ import annotations

import unittest

from small_paper.canonical_summary import (
    build_canonical_summary,
    collect_canonical_trades,
    enrich_summary_with_canonical,
    validate_canonical_summary_integrity,
)
from small_paper.discord_message_builder import (
    build_daily_summary_detail,
    format_discord_summary_lines,
)


def _exit(
    symbol: str,
    entry: float,
    exit_p: float,
    pnl_pct: float,
    *,
    exit_reason: str = "trailing_mfe_exit",
    **extra: object,
) -> dict:
    return {
        "event_type": "observer_exit",
        "symbol": symbol,
        "entry_price": entry,
        "exit_price": exit_p,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
        **extra,
    }


class TestCanonicalSummary(unittest.TestCase):
    def _canonical(self, events: list[dict]) -> dict:
        trades = collect_canonical_trades(events)
        return build_canonical_summary(trades, peak_open_slots=2, max_concurrent_positions=3)

    def test_all_trades_positive(self) -> None:
        events = [
            _exit("9984.T", 3000, 3030, 1.0),
            _exit("7203.T", 2500, 2525, 1.0),
        ]
        c = self._canonical(events)
        self.assertEqual(c["trade_count"], 2)
        self.assertEqual(c["win_count"], 2)
        self.assertGreater(c["total_pnl_yen_100"], 0)
        self.assertEqual(c["profit_factor_yen_100"], "inf")
        self.assertEqual(c["gross_loss_yen_100"], 0.0)

    def test_all_trades_negative(self) -> None:
        events = [
            _exit("6981.T", 1000, 990, -1.0, exit_reason="stop_hit"),
            _exit("7220.T", 2000, 1986, -0.7, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertEqual(c["trade_count"], 2)
        self.assertEqual(c["loss_count"], 2)
        self.assertLess(c["total_pnl_yen_100"], 0)
        self.assertEqual(c["profit_factor_yen_100"], 0.0)
        self.assertEqual(c["stop_count"], 2)

    def test_mixed_pf_greater_than_one(self) -> None:
        events = [
            _exit("9984.T", 1000, 1100, 10.0),
            _exit("7203.T", 1000, 950, -5.0, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertGreater(c["profit_factor_yen_100"], 1.0)
        self.assertGreater(c["total_pnl_yen_100"], 0)
        self.assertEqual(c["win_count"], 1)
        self.assertEqual(c["loss_count"], 1)

    def test_mixed_pf_less_than_one(self) -> None:
        events = [
            _exit("9984.T", 1000, 1010, 1.0),
            _exit("7203.T", 1000, 900, -10.0, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertLess(c["profit_factor_yen_100"], 1.0)
        self.assertLess(c["total_pnl_yen_100"], 0)

    def test_pf_near_one(self) -> None:
        events = [
            _exit("9984.T", 1000, 1100, 10.0),
            _exit("7203.T", 1000, 900, -10.0, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertAlmostEqual(float(c["profit_factor_yen_100"]), 1.0, places=4)
        self.assertAlmostEqual(c["total_pnl_yen_100"], 0.0, places=2)

    def test_gross_loss_zero_profit_factor_inf(self) -> None:
        events = [_exit("9984.T", 500, 510, 2.0)]
        c = self._canonical(events)
        self.assertEqual(c["gross_loss_yen_100"], 0.0)
        self.assertEqual(c["profit_factor_yen_100"], "inf")

    def test_total_pnl_pct_raw_diverges_from_yen_sign(self) -> None:
        events = [
            _exit("9984.T", 30000, 30300, 1.0),
            _exit("7203.T", 500, 490, -2.0, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertGreater(c["total_pnl_yen_100"], 0)
        self.assertLess(c["total_pnl_pct_raw"], 0)
        self.assertGreater(c["avg_pnl_yen_100"], 0)
        self.assertLess(c["avg_pnl_pct"], 0)
        lines = format_discord_summary_lines(c)
        detail = "\n".join(lines)
        self.assertNotIn("total_pnl_pct", detail)
        self.assertNotIn("avg_pnl_pct", detail)
        self.assertIn("平均損益:", detail)

    def test_duplicate_entry_not_in_exit_reason_display(self) -> None:
        events = [
            _exit(
                "3905.T",
                1000,
                1010,
                1.0,
                exit_reason="overlap_replaced_review",
                overlap_replaced_review=True,
                structural_exit_reason="trailing_mfe_exit",
            ),
        ]
        c = self._canonical(events)
        best = c["best_trade"]
        self.assertIsInstance(best, dict)
        assert isinstance(best, dict)
        self.assertTrue(best["duplicate_entry_observed"])
        self.assertNotIn("重複エントリー", best["display"])
        self.assertNotIn("overlap_replaced_review", best["display"])
        self.assertIn("トレーリング決済", best["display"])
        self.assertEqual(best["exit_reason"], "trailing_mfe_exit")

    def test_discord_matches_canonical_summary(self) -> None:
        events = [
            _exit("9984.T", 1000, 1050, 5.0),
            _exit("7203.T", 2000, 1980, -1.0, exit_reason="stop_hit"),
        ]
        c = self._canonical(events)
        self.assertEqual(build_daily_summary_detail(c), "\n".join(format_discord_summary_lines(c)))
        self.assertEqual(validate_canonical_summary_integrity(c, collect_canonical_trades(events)), [])

    def test_shadow_skipped_capacity_rejected_excluded(self) -> None:
        events = [
            _exit("9984.T", 1000, 1050, 5.0),
            {
                "event_type": "shadow_exit",
                "symbol": "7203.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
            },
            {
                "event_type": "rejected",
                "symbol": "6758.T",
                "gate_reject_reason": "max_concurrent",
            },
            {
                "event_type": "observer_exit",
                "symbol": "6758.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
                "exit_kind": "capacity_rejected",
            },
            {
                "event_type": "observer_exit",
                "symbol": "4063.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
                "skipped": True,
            },
            {
                "event_type": "observer_exit",
                "symbol": "8035.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
                "notification_only": True,
            },
            {
                "event_type": "debug",
                "symbol": "6501.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
            },
            {
                "event_type": "observer_exit",
                "symbol": "6501.T",
                "entry_price": 1000,
                "exit_price": 900,
                "pnl_pct": -10.0,
                "exit_reason": "live_virtual_hold",
            },
        ]
        trades = collect_canonical_trades(events)
        self.assertEqual(len(trades), 1)
        summary: dict = {}
        enrich_summary_with_canonical(
            summary,
            events,
            max_concurrent_positions=3,
            watch_symbols_count=50,
        )
        self.assertEqual(summary["canonical_summary"]["trade_count"], 1)
        self.assertNotIn("summary_integrity_error", summary)


if __name__ == "__main__":
    unittest.main()

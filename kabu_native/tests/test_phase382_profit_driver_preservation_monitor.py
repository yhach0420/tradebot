"""Phase382 profit driver preservation monitor tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase382_profit_driver_preservation_monitor import (  # noqa: E402
    Phase382ProfitDriverPreservationMonitor,
    aggregate_window_metrics,
    apply_low_mfe_spike_flags,
    compute_daily_driver_metrics,
    evaluate_preservation_shadow,
    evaluate_window_preservation_checks,
    load_baseline_reference,
)


class TestPhase382ProfitDriverPreservation(unittest.TestCase):
    def _trades(self) -> list[dict]:
        return [
            {
                "day_key": "20260601",
                "pnl_yen_100": 5000.0,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 1.2,
                "hold_seconds": 600.0,
                "universe_group": "dynamic40",
                "dynamic40_rank_bucket": "rank_31_40",
                "session_kind": "am",
                "board_dynamic_tier": "board_low",
            },
            {
                "day_key": "20260601",
                "pnl_yen_100": 3000.0,
                "exit_reason_canonical": "overlap_replaced",
                "peak_mfe_pct": 0.8,
                "hold_seconds": 400.0,
                "universe_group": "dynamic40",
                "dynamic40_rank_bucket": "rank_21_30",
                "session_kind": "pm",
                "board_dynamic_tier": "board_low",
            },
            {
                "day_key": "20260601",
                "pnl_yen_100": -1000.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.1,
                "hold_seconds": 100.0,
                "universe_group": "dynamic40",
                "dynamic40_rank_bucket": "rank_1_10",
                "session_kind": "am",
            },
            {
                "day_key": "20260602",
                "pnl_yen_100": 2000.0,
                "exit_reason_canonical": "overlap_replaced",
                "peak_mfe_pct": 0.5,
                "hold_seconds": 200.0,
                "universe_group": "core10",
                "dynamic40_rank_bucket": "core10_or_other",
                "session_kind": "am",
                "board_dynamic_tier": "board_high",
            },
        ]

    def test_compute_daily_driver_metrics(self) -> None:
        day_trades = [t for t in self._trades() if t["day_key"] == "20260601"]
        row = compute_daily_driver_metrics("20260601", day_trades)
        self.assertEqual(row["trade_count"], 3)
        self.assertEqual(row["winning_count"], 2)
        self.assertEqual(row["trailing_mfe_exit_count"], 1)
        self.assertEqual(row["overlap_replaced_count"], 1)
        self.assertEqual(row["board_low_winner_count"], 2)
        self.assertEqual(row["low_mfe_stop_hit_count"], 1)
        self.assertGreater(row["rank_21_40_winning_pnl"], 0)

    def test_aggregate_window_metrics(self) -> None:
        rows = [
            compute_daily_driver_metrics("20260601", [t for t in self._trades() if t["day_key"] == "20260601"]),
            compute_daily_driver_metrics("20260602", [t for t in self._trades() if t["day_key"] == "20260602"]),
        ]
        apply_low_mfe_spike_flags(rows, multiplier=2.5)
        wm = aggregate_window_metrics(rows)
        self.assertEqual(wm["trade_count"], 4)
        self.assertEqual(wm["winning_count"], 3)
        self.assertGreater(wm["trailing_mfe_exit_pnl"], 0)
        self.assertGreater(wm["overlap_replaced_pnl"], 0)

    def test_preservation_shadow_hurts(self) -> None:
        rows = evaluate_preservation_shadow(self._trades())
        overlap = next(r for r in rows if r["variant_id"] == "E_confirm_overlap_cut")
        trailing = next(r for r in rows if r["variant_id"] == "F_confirm_trailing_cut")
        self.assertTrue(overlap["would_hurt"])
        self.assertTrue(trailing["would_hurt"])
        self.assertLess(float(overlap["delta_yen"]), 0.0)
        self.assertLess(float(trailing["delta_yen"]), 0.0)

    def test_evaluate_window_preservation_checks(self) -> None:
        rows = [
            compute_daily_driver_metrics("20260601", [t for t in self._trades() if t["day_key"] == "20260601"]),
            compute_daily_driver_metrics("20260602", [t for t in self._trades() if t["day_key"] == "20260602"]),
        ]
        apply_low_mfe_spike_flags(rows, multiplier=2.5)
        wm = aggregate_window_metrics(rows)
        shadow = evaluate_preservation_shadow(self._trades())
        baseline = {
            "top100_overlap_share": 0.46,
            "top100_trailing_share": 0.41,
            "preserve_profile": ["trailing_mfe_exit", "overlap_replaced"],
        }
        result = evaluate_window_preservation_checks(
            by_day_rows=rows,
            window_metrics=wm,
            baseline=baseline,
            shadow_rows=shadow,
        )
        self.assertIn(result["preservation_status"], ("ok", "warn", "fail"))
        self.assertTrue(result["checks"]["overlap_cut_would_hurt"])
        self.assertTrue(result["checks"]["trailing_cut_would_hurt"])

    def test_load_baseline_reference(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        baseline = load_baseline_reference(reports)
        if baseline.get("loaded"):
            self.assertIn("trailing_mfe_pnl", baseline)
            self.assertIn("overlap_pnl", baseline)

    def test_monitor_analyze(self) -> None:
        monitor = Phase382ProfitDriverPreservationMonitor(
            reports_dir=REPO / "kabu_native" / "results" / "reports"
        )
        for trade in self._trades():
            monitor.all_trades.append(trade)
        result = monitor.analyze(sessions_discovered=1, sessions_evaluated=1)
        self.assertEqual(result["phase"], 382)
        self.assertIn("window_metrics", result)
        self.assertIn("by_day", result)
        self.assertEqual(len(result["by_day"]), 2)


if __name__ == "__main__":
    unittest.main()

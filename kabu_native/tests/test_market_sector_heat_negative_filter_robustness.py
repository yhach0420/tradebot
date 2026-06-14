"""Phase254-SectorHeat-Negative-Filter-Robustness tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat_negative_filter_robustness import (  # noqa: E402
    MarketSectorHeatNegativeFilterRobustness,
    build_day_level_stability_rows,
    build_entry_count_impact_rows,
    build_robustness_verdict,
    summarize_win_loss_days,
    run_robustness_analysis,
)


class TestMarketSectorHeatNegativeFilterRobustness(unittest.TestCase):
    def test_summarize_win_loss_days(self) -> None:
        rows = [
            {"pattern": "bottom3_exclude", "day": "20260520", "delta_pnl_yen_100": 5000.0},
            {"pattern": "bottom3_exclude", "day": "20260521", "delta_pnl_yen_100": -1000.0},
            {"pattern": "bottom3_exclude", "day": "20260522", "delta_pnl_yen_100": 350.0},
            {"pattern": "bottom3_exclude", "day": "20260525", "delta_pnl_yen_100": 700.0},
        ]
        summary = summarize_win_loss_days(rows, pattern="bottom3_exclude")
        self.assertEqual(summary["delta_positive_days"], 3)
        self.assertEqual(summary["delta_negative_days"], 1)
        self.assertEqual(summary["delta_positive_rate"], 0.75)
        self.assertEqual(summary["fragile_single_day"], "20260520")

    def test_build_robustness_verdict_adopt_not_allowed(self) -> None:
        win_loss = {
            "pattern": "bottom3_exclude",
            "trade_overlap_day_count": 4,
            "delta_positive_rate": 0.75,
            "fragile_single_day": "20260520",
            "fragile_single_day_share_of_total_delta": 0.99,
        }
        verdict = build_robustness_verdict(
            pattern="bottom3_exclude",
            win_loss=win_loss,
            entry_rows=[],
            exclusion_rows=[],
            trade_overlap_day_count=4,
        )
        self.assertTrue(verdict["adopt_not_allowed"])
        self.assertFalse(verdict["stable_candidate"])

    def test_build_day_level_stability_rows(self) -> None:
        rows = build_day_level_stability_rows(
            [
                {
                    "day": "20260520",
                    "signal_day": "20260519",
                    "pattern": "bottom3_exclude",
                    "actual_pnl_yen_100": -5000.0,
                    "shadow_pnl_yen_100": 0.0,
                    "delta_pnl_yen_100": 5000.0,
                    "removed_loser_avoidance_yen_100": 9100.0,
                    "added_winner_contribution_yen_100": 0.0,
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["delta_positive"])

    def test_build_entry_count_impact_rows(self) -> None:
        trade_rows = [
            {"day": "20260520", "pattern": "actual", "entry_count": 20, "total_pnl_yen_100": -5000.0},
            {
                "day": "20260520",
                "pattern": "bottom3_exclude",
                "entry_count": 0,
                "total_pnl_yen_100": 0.0,
                "delta_pnl_yen_100_vs_actual": 5000.0,
            },
        ]
        rows = build_entry_count_impact_rows(trade_rows, trade_overlap_days=["20260520"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_count_delta"], -20)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_sector_heat_negative_filter_summary.json").is_file():
            self.skipTest("phase253 summary missing")
        result = run_robustness_analysis(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "254-SectorHeat-Negative-Filter-Robustness")
        self.assertEqual(len(result.get("robustness_verdict_by_pattern") or []), 5)

    def test_write_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_sector_heat_negative_filter_summary.json").is_file():
            self.skipTest("phase253 summary missing")
        job = MarketSectorHeatNegativeFilterRobustness(repo_root=REPO, reports_dir=reports)
        result = job.run()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            job_out = MarketSectorHeatNegativeFilterRobustness(repo_root=REPO, reports_dir=out)
            paths = job_out.write_outputs(result)
            for key in (
                "summary",
                "day_level_stability",
                "entry_count_impact",
                "exclusion_severity",
                "report",
            ):
                self.assertTrue(paths[key].is_file(), key)


if __name__ == "__main__":
    unittest.main()

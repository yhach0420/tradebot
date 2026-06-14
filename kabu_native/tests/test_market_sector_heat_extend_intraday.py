"""Phase251-SectorHeat-Extend-Intraday-Data tests."""

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

from research.market_sector_heat_extend_intraday import (  # noqa: E402
    TARGET_MAX_DAY,
    TARGET_MIN_DAY,
    apply_backfill_status_to_gap_rows,
    build_intraday_gap_report_rows,
    count_bars_in_csv,
    resolve_target_backfill_days,
    summarize_gap_report,
)


class TestMarketSectorHeatExtendIntraday(unittest.TestCase):
    def test_count_bars_in_csv_missing(self) -> None:
        self.assertEqual(count_bars_in_csv(Path("/nonexistent/file.csv")), 0)

    def test_resolve_target_backfill_days_from_reports(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not reports.is_dir():
            self.skipTest("reports dir missing")
        days = resolve_target_backfill_days(
            reports_dir=reports,
            min_day=TARGET_MIN_DAY,
            max_day=TARGET_MAX_DAY,
        )
        self.assertTrue(days)
        self.assertGreaterEqual(days[0], TARGET_MIN_DAY)
        self.assertLessEqual(days[-1], TARGET_MAX_DAY)

    def test_gap_report_shape(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        symbols = ["7203.T", "9984.T"]
        rows = build_intraday_gap_report_rows(
            repo_root=REPO,
            reports_dir=reports,
            symbols=symbols,
            target_days=["20260519", "20260520"],
        )
        self.assertEqual(len(rows), 4)
        summary = summarize_gap_report(rows)
        self.assertEqual(summary["target_day_count"], 2)
        self.assertEqual(summary["symbol_count"], 2)

    def test_apply_backfill_status_window_out(self) -> None:
        rows = [
            {
                "day": "20260401",
                "symbol": "7203.T",
                "has_intraday_csv": False,
                "in_yahoo_1m_window": False,
                "backfill_status": "missing",
                "backfill_note": "",
            }
        ]
        apply_backfill_status_to_gap_rows(rows, {"failures": []})
        self.assertEqual(rows[0]["backfill_status"], "window_out")

    def test_run_on_repo_skip_backfill(self) -> None:
        from research.market_sector_heat_extend_intraday import MarketSectorHeatExtendIntradayData

        reports = REPO / "kabu_native" / "results" / "reports"
        job = MarketSectorHeatExtendIntradayData(
            repo_root=REPO,
            reports_dir=reports,
            skip_backfill=True,
        )
        result = job.run()
        self.assertEqual(result["phase"], "251-SectorHeat-Extend-Intraday-Data")
        self.assertIn("phase246_rerun", result)
        self.assertIn("phase249_rerun", result)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            job_tmp = MarketSectorHeatExtendIntradayData(
                repo_root=REPO,
                reports_dir=out_dir,
                skip_backfill=True,
            )
            result2 = job_tmp.run()
            paths = job_tmp.write_outputs(result2)
            self.assertTrue(paths["summary"].is_file())
            self.assertTrue(paths["gap_report"].is_file())
            self.assertTrue(paths["phase249_rerun"].is_file())
            self.assertTrue(paths["report"].is_file())


if __name__ == "__main__":
    unittest.main()

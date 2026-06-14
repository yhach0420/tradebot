"""Phase253-SectorHeat-Negative-Filter-Shadow tests."""

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

from research.market_sector_heat_negative_filter_shadow import (  # noqa: E402
    MarketSectorHeatNegativeFilterShadow,
    excluded_sectors_for_pattern,
    filter_candidates,
    load_sector_rows_by_day,
    run_negative_filter_shadow,
    select_negative_filter_dynamic40,
)


class TestMarketSectorHeatNegativeFilterShadow(unittest.TestCase):
    def test_excluded_sectors_bottom3(self) -> None:
        sector_rows = {
            "20260519": [
                {"sector_33_name": "A", "heat_score": "1.0", "daily_return_pct": "1.0"},
                {"sector_33_name": "B", "heat_score": "2.0", "daily_return_pct": "1.0"},
                {"sector_33_name": "C", "heat_score": "3.0", "daily_return_pct": "1.0"},
                {"sector_33_name": "D", "heat_score": "4.0", "daily_return_pct": "1.0"},
            ]
        }
        excluded = excluded_sectors_for_pattern("bottom3_exclude", "20260519", sector_rows)
        self.assertEqual(excluded, {"A", "B", "C"})

    def test_excluded_sectors_negative_return(self) -> None:
        sector_rows = {
            "20260519": [
                {"sector_33_name": "A", "heat_score": "1.0", "daily_return_pct": "-1.0"},
                {"sector_33_name": "B", "heat_score": "2.0", "daily_return_pct": "1.0"},
            ]
        }
        excluded = excluded_sectors_for_pattern(
            "negative_return_sector_exclude",
            "20260519",
            sector_rows,
        )
        self.assertEqual(excluded, {"A"})

    def test_filter_candidates(self) -> None:
        candidates = [
            {"symbol": "7203.T", "sector_33_name": "A", "volatility_liquidity_score": 1.0},
            {"symbol": "9984.T", "sector_33_name": "B", "volatility_liquidity_score": 2.0},
        ]
        out = filter_candidates(candidates, {"A"})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "9984.T")

    def test_select_negative_filter_dynamic40(self) -> None:
        candidates = [
            {
                "symbol": "7203.T",
                "sector_33_name": "A",
                "volatility_liquidity_score": 1.0,
                "sector_heat_rank_num": None,
            },
            {
                "symbol": "9984.T",
                "sector_33_name": "B",
                "volatility_liquidity_score": 2.0,
                "sector_heat_rank_num": None,
            },
        ]
        syms, ranks = select_negative_filter_dynamic40(
            candidates,
            pattern="bottom3_exclude",
            actual_dynamic={"7203.T"},
            actual_rank_map={"7203.T": 1},
            top3_map={},
        )
        self.assertIn("9984.T", syms)
        self.assertEqual(ranks["9984.T"], 1)

    def test_load_sector_rows_by_day(self) -> None:
        path = REPO / "kabu_native" / "results" / "reports" / "phase246_sector_heat_by_sector.csv"
        if not path.is_file():
            self.skipTest("phase246 by_sector missing")
        by_day = load_sector_rows_by_day(path)
        self.assertGreater(len(by_day), 0)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        result = run_negative_filter_shadow(
            repo_root=REPO,
            reports_dir=reports,
            by_sector_path=reports / "phase246_sector_heat_by_sector.csv",
            top3_path=reports / "phase246_sector_heat_tomorrow_top3.csv",
            jpx_path=REPO / "data" / "jpx" / "tradable_symbols.csv",
        )
        self.assertEqual(result["phase"], "253-SectorHeat-Negative-Filter-Shadow")
        self.assertGreater(result["coverage"]["simulated_day_count"], 0)

    def test_write_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        job = MarketSectorHeatNegativeFilterShadow(repo_root=REPO, reports_dir=reports)
        result = job.run()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            job_out = MarketSectorHeatNegativeFilterShadow(repo_root=REPO, reports_dir=out)
            paths = job_out.write_outputs(result)
            for key in (
                "summary",
                "universe_diff_by_day",
                "trade_validation_by_pattern",
                "added_removed",
                "day_level_delta",
                "report",
            ):
                self.assertTrue(paths[key].is_file(), key)


if __name__ == "__main__":
    unittest.main()

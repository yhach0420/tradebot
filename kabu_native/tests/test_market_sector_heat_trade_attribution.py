"""Phase252-SectorHeat-Trade-Attribution tests."""

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

from research.market_sector_heat_trade_attribution import (  # noqa: E402
    MarketSectorHeatTradeAttribution,
    discover_trade_overlap_days,
    overlap_ratio,
    parse_pipe_symbols,
    primary_driver,
    run_trade_attribution,
)


class TestMarketSectorHeatTradeAttribution(unittest.TestCase):
    def test_parse_pipe_symbols(self) -> None:
        syms = parse_pipe_symbols("7203.T|9984.T|")
        self.assertEqual(syms, {"7203.T", "9984.T"})

    def test_overlap_ratio(self) -> None:
        self.assertEqual(overlap_ratio({"A", "B"}, {"B", "C"}), 0.3333)

    def test_primary_driver(self) -> None:
        self.assertEqual(primary_driver(-5000.0, 1000.0, 6000.0), "avoided_loss")
        self.assertEqual(primary_driver(-500.0, 5000.0, 5500.0), "added_edge")

    def test_discover_trade_overlap_days(self) -> None:
        rows = [
            {"day": "20260520", "pattern": "actual", "entry_count": "20"},
            {"day": "20260521", "pattern": "actual", "entry_count": "0"},
        ]
        trades = {"20260520": [{}], "20260521": [{}]}
        self.assertEqual(
            discover_trade_overlap_days(rows, trades),
            ["20260520", "20260521"],
        )

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase249_trade_validation_by_pattern.csv").is_file():
            self.skipTest("phase249 trade validation missing")
        result = run_trade_attribution(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "252-SectorHeat-Trade-Attribution")
        self.assertGreater(len(result.get("trade_overlap_days") or []), 0)
        self.assertGreater(len(result.get("_added_removed_rows") or []), 0)

    def test_write_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase249_trade_validation_by_pattern.csv").is_file():
            self.skipTest("phase249 trade validation missing")
        job = MarketSectorHeatTradeAttribution(repo_root=REPO, reports_dir=reports)
        result = job.run()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            job_out = MarketSectorHeatTradeAttribution(repo_root=REPO, reports_dir=out)
            paths = job_out.write_outputs(result)
            for key in (
                "summary",
                "added_removed",
                "avoided_loss",
                "pattern_similarity",
                "day_level_delta",
                "report",
            ):
                self.assertTrue(paths[key].is_file(), key)


if __name__ == "__main__":
    unittest.main()

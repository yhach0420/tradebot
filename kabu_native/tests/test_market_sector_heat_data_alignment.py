"""Phase250-SectorHeat-Data-Alignment-Diagnostics tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat_data_alignment import (  # noqa: E402
    MarketSectorHeatDataAlignmentDiagnostics,
    build_next_action_suggestions,
    missing_reason_for_day,
    run_data_alignment_diagnostics,
)


class TestMarketSectorHeatDataAlignment(unittest.TestCase):
    def test_missing_reason_ready(self) -> None:
        reason = missing_reason_for_day(
            day="20260520",
            has_top3=True,
            signal_day="20260519",
            has_features_on_day=True,
            has_features_on_signal_day=True,
            has_universe=True,
            has_trades=True,
            can_simulate=True,
            can_validate=True,
        )
        self.assertEqual(reason, "ready")

    def test_missing_reason_phase249_gap(self) -> None:
        reason = missing_reason_for_day(
            day="20260518",
            has_top3=True,
            signal_day="20260515",
            has_features_on_day=False,
            has_features_on_signal_day=False,
            has_universe=False,
            has_trades=False,
            can_simulate=False,
            can_validate=False,
        )
        self.assertIn("missing_features_for_signal_day_20260515", reason)
        self.assertIn("missing_universe_snapshot", reason)

    def test_next_action_suggestions_gap(self) -> None:
        suggestions = build_next_action_suggestions(
            sector_heat_days={"20260515", "20260518"},
            feature_days={"20260519", "20260520"},
            universe_days={"20260519", "20260520"},
            trade_days={"20260519", "20260520"},
            intraday_days={"20260410", "20260515"},
            signal_by_validation={"20260518": "20260515"},
            by_day_rows=[{"can_simulate_universe": False, "can_validate_trade": False}],
        )
        joined = " ".join(suggestions)
        self.assertIn("Phase246", joined)
        self.assertIn("Phase249", joined)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        result = run_data_alignment_diagnostics(repo_root=REPO, reports_dir=reports)
        self.assertTrue(result["coverage"]["phase249_blocked"])
        self.assertGreater(result["coverage"]["calendar_day_count"], 0)
        self.assertGreater(len(result["data_source_ranges"]), 0)

    def test_write_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        audit = MarketSectorHeatDataAlignmentDiagnostics(repo_root=REPO, reports_dir=reports)
        result = audit.run()
        paths = audit.write_outputs(result)
        self.assertTrue(paths["summary"].is_file())
        self.assertTrue(paths["by_day"].is_file())


if __name__ == "__main__":
    unittest.main()

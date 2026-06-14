"""Phase247-SectorHeat-Diagnostics tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat_diagnostics import (  # noqa: E402
    MarketSectorHeatDiagnostics,
    build_baseline_comparison,
    build_overfit_warnings,
    build_rank_continuation_rows,
    build_sector_concentration,
    build_sector_validation_rows,
    run_diagnostics_from_inputs,
)


def _sample_inputs() -> tuple[list[dict], dict[str, dict[str, dict]]]:
    tomorrow_top3 = [
        {"signal_day": "20260410", "validation_day": "20260413", "rank": 1, "sector_33_name": "A"},
        {"signal_day": "20260410", "validation_day": "20260413", "rank": 2, "sector_33_name": "B"},
        {"signal_day": "20260410", "validation_day": "20260413", "rank": 3, "sector_33_name": "C"},
        {"signal_day": "20260413", "validation_day": "20260414", "rank": 1, "sector_33_name": "A"},
        {"signal_day": "20260413", "validation_day": "20260414", "rank": 2, "sector_33_name": "B"},
        {"signal_day": "20260413", "validation_day": "20260414", "rank": 3, "sector_33_name": "C"},
    ]
    sector_rows_by_day = {
        "20260413": {
            "A": {"daily_return_pct": 1.0},
            "B": {"daily_return_pct": -0.5},
            "C": {"daily_return_pct": 0.2},
            "D": {"daily_return_pct": -1.0},
        },
        "20260414": {
            "A": {"daily_return_pct": 2.0},
            "B": {"daily_return_pct": 1.0},
            "C": {"daily_return_pct": -0.3},
            "D": {"daily_return_pct": 0.1},
        },
    }
    return tomorrow_top3, sector_rows_by_day


class TestMarketSectorHeatDiagnostics(unittest.TestCase):
    def test_sector_concentration(self) -> None:
        top3, _ = _sample_inputs()
        conc = build_sector_concentration(top3)
        self.assertEqual(conc["top3_slot_count"], 6)
        self.assertEqual(conc["unique_sectors_in_top3"], 3)
        self.assertEqual(conc["top3_sector_slot_share"], 1.0)
        by_sector = {r["sector_33_name"]: r for r in conc["by_sector"]}
        self.assertEqual(by_sector["A"]["rank1_count"], 2)
        self.assertEqual(by_sector["A"]["top3_count"], 2)

    def test_rank_continuation(self) -> None:
        top3, sector_rows = _sample_inputs()
        rows = build_rank_continuation_rows(top3, sector_rows_by_day=sector_rows)
        self.assertEqual(len(rows), 3)
        rank1 = next(r for r in rows if r["rank"] == 1)
        self.assertEqual(rank1["signal_count"], 2)
        self.assertEqual(rank1["next_day_positive_count"], 2)
        self.assertEqual(rank1["continuation_rate"], 1.0)

    def test_baseline_comparison(self) -> None:
        top3, sector_rows = _sample_inputs()
        baseline = build_baseline_comparison(top3, sector_rows_by_day=sector_rows)
        self.assertEqual(baseline["all_sectors_next_day_positive_rate"], 0.625)
        self.assertEqual(baseline["top3_next_day_positive_rate"], 0.6667)
        self.assertAlmostEqual(baseline["top3_vs_all_sectors_positive_rate_delta"], 0.0417, places=3)

    def test_sector_validation_and_overfit(self) -> None:
        top3, sector_rows = _sample_inputs()
        conc = build_sector_concentration(top3)
        sector_rows_out = build_sector_validation_rows(
            top3,
            sector_rows_by_day=sector_rows,
            total_top3_slots=conc["top3_slot_count"],
            min_trusted_signal_count=2,
        )
        self.assertEqual(len(sector_rows_out), 3)
        self.assertFalse(any(r["reference_only"] for r in sector_rows_out))
        overfit = build_overfit_warnings(conc, sector_rows_out)
        self.assertIn("top3_sector_concentration_high", overfit["flags"])
        self.assertEqual(overfit["verdict"], "likely_sector_bias")

    def test_run_diagnostics_from_inputs(self) -> None:
        top3, sector_rows = _sample_inputs()
        result = run_diagnostics_from_inputs(
            tomorrow_top3=top3,
            sector_rows_by_day=sector_rows,
        )
        self.assertTrue(result["constraints"]["entry_change_forbidden"])
        self.assertEqual(len(result["by_rank"]), 3)
        self.assertEqual(len(result["_by_sector_validation_rows"]), 3)

    def test_run_on_repo_phase246_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        top3_path = reports / "phase246_sector_heat_tomorrow_top3.csv"
        if not top3_path.is_file():
            self.skipTest("phase246 outputs missing")
        audit = MarketSectorHeatDiagnostics(repo_root=REPO, reports_dir=reports)
        result = audit.run()
        paths = audit.write_outputs(result)
        self.assertTrue(paths["diagnostics"].is_file())
        self.assertTrue(paths["by_rank"].is_file())
        self.assertTrue(paths["by_sector_validation"].is_file())
        self.assertIn("baseline_comparison", result)
        self.assertIn("overfit_warnings", result)


if __name__ == "__main__":
    unittest.main()

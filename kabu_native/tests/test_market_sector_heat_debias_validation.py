"""Phase248-SectorHeat-Debias-Validation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat import _float  # noqa: E402
from research.market_sector_heat_debias_validation import (  # noqa: E402
    MarketSectorHeatDebiasValidation,
    apply_sector_adoption_cap,
    build_capped_sector_validation_rows,
    build_leave_one_sector_out_rows,
    build_rank_heat_profile_rows,
    build_sector_neutral_validation,
    run_debias_validation_from_inputs,
)


def _sample_inputs() -> tuple[list[dict], dict[str, dict[str, dict]]]:
    tomorrow_top3 = [
        {
            "signal_day": "20260410",
            "validation_day": "20260413",
            "rank": 1,
            "sector_33_name": "非鉄金属",
            "heat_score": 3.0,
            "daily_return_pct": 10.0,
            "pm_return_pct_1400_1530": 5.0,
        },
        {
            "signal_day": "20260410",
            "validation_day": "20260413",
            "rank": 2,
            "sector_33_name": "電気機器",
            "heat_score": 2.5,
            "daily_return_pct": 5.0,
            "pm_return_pct_1400_1530": 4.0,
        },
        {
            "signal_day": "20260410",
            "validation_day": "20260413",
            "rank": 3,
            "sector_33_name": "情報・通信業",
            "heat_score": 2.0,
            "daily_return_pct": 3.0,
            "pm_return_pct_1400_1530": 2.0,
        },
        {
            "signal_day": "20260413",
            "validation_day": "20260414",
            "rank": 1,
            "sector_33_name": "非鉄金属",
            "heat_score": 3.2,
            "daily_return_pct": 8.0,
            "pm_return_pct_1400_1530": 4.5,
        },
        {
            "signal_day": "20260413",
            "validation_day": "20260414",
            "rank": 2,
            "sector_33_name": "電気機器",
            "heat_score": 2.8,
            "daily_return_pct": 4.0,
            "pm_return_pct_1400_1530": 3.5,
        },
        {
            "signal_day": "20260413",
            "validation_day": "20260414",
            "rank": 3,
            "sector_33_name": "情報・通信業",
            "heat_score": 2.1,
            "daily_return_pct": 2.0,
            "pm_return_pct_1400_1530": 1.0,
        },
    ]
    sector_rows_by_day = {
        "20260413": {
            "非鉄金属": {"daily_return_pct": 2.0},
            "電気機器": {"daily_return_pct": 1.0},
            "情報・通信業": {"daily_return_pct": -1.0},
            "機械": {"daily_return_pct": 0.0},
        },
        "20260414": {
            "非鉄金属": {"daily_return_pct": -0.5},
            "電気機器": {"daily_return_pct": 3.0},
            "情報・通信業": {"daily_return_pct": 0.5},
            "機械": {"daily_return_pct": -2.0},
        },
    }
    return tomorrow_top3, sector_rows_by_day


class TestMarketSectorHeatDebiasValidation(unittest.TestCase):
    def test_sector_neutral_validation(self) -> None:
        top3, sector_rows = _sample_inputs()
        neutral = build_sector_neutral_validation(top3, sector_rows_by_day=sector_rows)
        self.assertEqual(neutral["signal_count"], 6)
        self.assertGreater(_float(neutral["beat_median_rate"]), 0.5)
        self.assertIsNotNone(neutral["avg_excess_vs_median_pct"])

    def test_leave_one_sector_out(self) -> None:
        top3, sector_rows = _sample_inputs()
        rows = build_leave_one_sector_out_rows(top3, sector_rows_by_day=sector_rows)
        self.assertEqual(len(rows), 4)
        none_row = next(r for r in rows if r["excluded_sector"] == "none")
        excluded = next(r for r in rows if r["excluded_sector"] == "非鉄金属")
        self.assertEqual(none_row["signal_count"], 6)
        self.assertEqual(excluded["signal_count"], 4)

    def test_apply_sector_cap(self) -> None:
        top3, _ = _sample_inputs()
        capped, max_adoptions = apply_sector_adoption_cap(top3, 0.30)
        self.assertEqual(max_adoptions, 1)
        self.assertLessEqual(len(capped), 3)

    def test_rank_heat_profile(self) -> None:
        top3, sector_rows = _sample_inputs()
        rows = build_rank_heat_profile_rows(top3, sector_rows_by_day=sector_rows)
        rank1 = next(r for r in rows if r["rank"] == 1)
        rank2 = next(r for r in rows if r["rank"] == 2)
        self.assertGreater(rank1["signal_day_heat_score_avg"], rank2["signal_day_heat_score_avg"])
        self.assertGreater(
            rank2["next_day_positive_rate"],
            rank1["next_day_positive_rate"],
        )

    def test_capped_validation_rows(self) -> None:
        top3, sector_rows = _sample_inputs()
        rows = build_capped_sector_validation_rows(top3, sector_rows_by_day=sector_rows)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["cap_pct"], "uncapped")

    def test_run_on_repo_phase246_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 outputs missing")
        audit = MarketSectorHeatDebiasValidation(repo_root=REPO, reports_dir=reports)
        result = audit.run()
        paths = audit.write_outputs(result)
        self.assertTrue(paths["summary"].is_file())
        self.assertTrue(result["constraints"]["entry_change_forbidden"])
        self.assertIn("sector_neutral_validation", result)


if __name__ == "__main__":
    unittest.main()

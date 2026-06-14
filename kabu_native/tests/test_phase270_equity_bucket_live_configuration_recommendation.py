"""Phase270 equity bucket live configuration recommendation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase270_equity_bucket_live_configuration_recommendation import (  # noqa: E402
    EQUITY_BUCKETS,
    build_adoption_verdict_label,
    build_required_answers,
    csv_row_to_result,
    pick_recommended,
    recommendation_row,
)


class TestPhase270EquityBucketRecommendation(unittest.TestCase):
    def test_csv_row_to_result(self) -> None:
        row = csv_row_to_result(
            {
                "starting_equity": "1500000",
                "leverage": "2.0",
                "cap": "3",
                "stop_policy": "fixed_stop_1p2",
                "final_equity": "1650270.0",
                "adoptable_by_final_equity": "True",
                "safe_configuration": "True",
                "days_below_50pct": "0",
                "max_drawdown_pct": "5.8746",
            }
        )
        self.assertEqual(row["starting_equity"], 1500000)
        self.assertTrue(row["adoptable_by_final_equity"])

    def test_adoption_verdict_label(self) -> None:
        self.assertEqual(
            build_adoption_verdict_label({"starting_equity": 1500000, "final_equity": 1400000, "days_below_50pct": 0}),
            "reject_final_equity",
        )
        self.assertEqual(
            build_adoption_verdict_label({"starting_equity": 1500000, "final_equity": 1600000, "days_below_50pct": 0, "max_drawdown_pct": 25}),
            "caution_high_drawdown",
        )

    def test_pick_recommended_prefers_safe(self) -> None:
        rows = [
            {"starting_equity": 1500000, "final_equity": 1700000, "max_drawdown_pct": 25, "days_below_50pct": 0, "adoptable_by_final_equity": True, "safe_configuration": False, "cap": 5},
            {"starting_equity": 1500000, "final_equity": 1650000, "max_drawdown_pct": 10, "days_below_50pct": 0, "adoptable_by_final_equity": True, "safe_configuration": True, "cap": 3},
        ]
        rec, basis = pick_recommended(rows)
        assert rec is not None
        self.assertEqual(rec["cap"], 3)
        self.assertEqual(basis, "safe_best_final_equity")

    def test_run_on_repo(self) -> None:
        from research.equity_curve_shadow import load_period_trades
        from research.phase270_equity_bucket_live_configuration_recommendation import (
            run_equity_bucket_live_configuration_recommendation,
        )

        trades, _ = load_period_trades(REPO)
        if not trades:
            self.skipTest("no period trades")
        result = run_equity_bucket_live_configuration_recommendation(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
        )
        self.assertEqual(result["phase"], "270-Equity-Bucket-Live-Configuration-Recommendation")
        self.assertEqual(len(result.get("equity_bucket_recommendations") or []), len(EQUITY_BUCKETS))
        answers = result.get("required_answers") or {}
        self.assertIn("1_cap_for_1500k", answers)
        rec1500 = answers.get("1500k_start_recommendation") or {}
        self.assertEqual(rec1500.get("recommended_cap"), 3)


if __name__ == "__main__":
    unittest.main()

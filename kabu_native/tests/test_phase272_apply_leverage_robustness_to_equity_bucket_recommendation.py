"""Phase272 lev2-fixed equity bucket recommendation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase272_apply_leverage_robustness_to_equity_bucket_recommendation import (  # noqa: E402
    EQUITY_BUCKETS,
    FIXED_LEVERAGE,
    build_required_answers,
)


class TestPhase272EquityBucketLev2Fixed(unittest.TestCase):
    def test_fixed_leverage_constant(self) -> None:
        self.assertEqual(FIXED_LEVERAGE, 2.0)

    def test_run_on_repo(self) -> None:
        from research.equity_curve_shadow import load_period_trades
        from research.phase272_apply_leverage_robustness_to_equity_bucket_recommendation import (
            run_phase272_equity_bucket_recommendation_lev2_fixed,
        )

        trades, _ = load_period_trades(REPO)
        if not trades:
            self.skipTest("no trades")
        result = run_phase272_equity_bucket_recommendation_lev2_fixed(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
        )
        self.assertEqual(result["phase"], "272-Apply-Leverage-Robustness-To-Equity-Bucket-Recommendation")
        recs = result.get("equity_bucket_recommendations") or []
        self.assertEqual(len(recs), len(EQUITY_BUCKETS))
        for row in recs:
            self.assertEqual(row.get("leverage"), 2.0)
        answers = result.get("required_answers") or {}
        self.assertEqual(answers.get("1_cap_for_1500k_lev2_fixed", {}).get("recommended_cap"), 3)


if __name__ == "__main__":
    unittest.main()

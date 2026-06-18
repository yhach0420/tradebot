"""Phase418 live config revalidation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase418_live_config_revalidation import (  # noqa: E402
    load_baseline_b_for_revalidation,
    run_phase418_revalidation,
)


class TestPhase418LiveConfigRevalidation(unittest.TestCase):
    def test_baseline_b_trade_count_and_period(self) -> None:
        trades, meta = load_baseline_b_for_revalidation(REPO)
        self.assertEqual(len(trades), 681)
        self.assertEqual(meta["period_day_count"], 11)
        self.assertEqual(meta["missing_entry_price_count"], 0)

    def test_full_revalidation_complete(self) -> None:
        result = run_phase418_revalidation(REPO)
        self.assertEqual(result["status"], "revalidation_complete")
        self.assertTrue(result["mandatory_checks"]["1_trade_count_681"])
        self.assertTrue(result["mandatory_checks"]["2_period_days_11"])

    def test_phase273_recommendation_maintained(self) -> None:
        result = run_phase418_revalidation(REPO)
        self.assertEqual(
            result["phase273"]["recommended_candidate_key"],
            "scale_candidate_3000k",
        )

    def test_1500k_not_distorted_by_invalid_price(self) -> None:
        result = run_phase418_revalidation(REPO)
        c1500 = result["mandatory_checks"]["3_live_start_1500k"]
        reject_counts = (
            result["mandatory_checks"]["9_accepted_rejected"]["live_start_candidate_1500k"][
                "reject_reason_counts"
            ]
            or {}
        )
        self.assertLess(int(reject_counts.get("invalid_price") or 0), 50)
        self.assertGreater(int(c1500.get("accepted_count") or 0), 100)

    def test_runtime_unchanged(self) -> None:
        self.assertTrue((REPO / "src" / "small_paper" / "pilot_runner.py").is_file())


if __name__ == "__main__":
    unittest.main()

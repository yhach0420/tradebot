"""Phase544 entry feature attribution unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase544_entry_feature_attribution import (  # noqa: E402
    ALL_FEATURES,
    PHASE544_VERDICT,
    _cohort_flags,
    _entropy,
    _feature_value,
    _information_gain,
    _pearson,
)


class TestPhase544EntryFeatureAttribution(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE544_VERDICT, "phase544_entry_feature_attribution_done")

    def test_feature_count(self) -> None:
        self.assertGreaterEqual(len(ALL_FEATURES), 25)

    def test_cohort_flags(self) -> None:
        row = {"pnl_yen_100": 100, "peak_mfe_pct": 1.5, "exit_reason": "trailing_mfe_exit"}
        flags = _cohort_flags(row)
        self.assertTrue(flags["winner"])
        self.assertTrue(flags["big_winner"])

    def test_bool_feature_value(self) -> None:
        self.assertEqual(_feature_value({"high_update_recent": True}, "high_update_recent"), 1.0)

    def test_information_gain(self) -> None:
        xs = [float(i) for i in range(40)]
        ys = [1 if i > 20 else 0 for i in range(40)]
        ig = _information_gain(xs, ys)
        self.assertIsNotNone(ig)
        self.assertGreater(ig or 0, 0)

    def test_pearson(self) -> None:
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(_pearson(a, a) or 0, 1.0, places=3)

    def test_entropy(self) -> None:
        self.assertAlmostEqual(_entropy([0, 0, 1, 1]), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()

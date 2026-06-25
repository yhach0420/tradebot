"""Phase545C feature engineering tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase545c_feature_engineering_hidden_loss_cluster import (  # noqa: E402
    ENGINEERED_FEATURES,
    PHASE545C_VERDICT,
    _assign_cohorts,
    _sub_label,
)


class TestPhase545CFeatureEngineering(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE545C_VERDICT, "phase545c_feature_engineering_hidden_loss_cluster_done")

    def test_feature_count(self) -> None:
        self.assertEqual(len(ENGINEERED_FEATURES), 20)

    def test_cohort_sub1(self) -> None:
        tags = _assign_cohorts({"subcluster_id": 1, "cluster_id": 3})
        self.assertIn("sub1", tags)

    def test_sub_label_exhaustion(self) -> None:
        g = {"exhaustion_score": 0.1}
        c = {"exhaustion_score": 0.2}
        self.assertEqual(_sub_label(c, g), "枯渇型")


if __name__ == "__main__":
    unittest.main()

"""Phase545B recursive cluster refinement tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase545b_recursive_cluster_refinement import (  # noqa: E402
    PHASE545B_VERDICT,
    PARENT_CLUSTER_ID,
    _composite,
    _subcluster_label,
    _trend_enc,
)


class TestPhase545BRecursiveClusterRefinement(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE545B_VERDICT, "phase545b_recursive_cluster_refinement_done")

    def test_parent_cluster(self) -> None:
        self.assertEqual(PARENT_CLUSTER_ID, 3)

    def test_trend_enc(self) -> None:
        self.assertEqual(_trend_enc("up"), 1.0)
        self.assertEqual(_trend_enc("down"), -1.0)

    def test_subcluster_label_chase(self) -> None:
        g = {"adx14": 20.0, "five_min_position": 40.0, "volume_percentile": 60.0}
        c = {"adx14": 35.0, "five_min_position": 70.0, "volume_percentile": 40.0}
        self.assertEqual(_subcluster_label(c, g), "遅延追いかけ")

    def test_composite(self) -> None:
        score = _composite({"silhouette": 0.2, "davies_bouldin": 1.5, "calinski_harabasz": 200.0})
        self.assertGreater(score, 0)


if __name__ == "__main__":
    unittest.main()

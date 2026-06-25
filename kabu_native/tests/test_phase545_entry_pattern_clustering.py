"""Phase545 entry pattern clustering unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase545_entry_pattern_clustering import (  # noqa: E402
    PHASE545_VERDICT,
    _augment_features,
    _cluster_label,
    _profit_source,
)


class TestPhase545EntryPatternClustering(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE545_VERDICT, "phase545_entry_pattern_clustering_done")

    def test_volume_surge(self) -> None:
        row: dict = {"volume_percentile": "85", "volume_ratio": "1.5"}
        _augment_features(row)
        self.assertEqual(row["volume_surge"], 1.0)

    def test_cluster_label_chase(self) -> None:
        global_med = {"adx14": 20.0, "five_min_position": 40.0, "board_imbalance": 0.5}
        centroid = {"adx14": 35.0, "five_min_position": 70.0, "board_imbalance": 0.4}
        self.assertEqual(_cluster_label(centroid, global_med), "遅延追いかけ型")

    def test_profit_source(self) -> None:
        rows = _profit_source(
            [
                {"cluster_id": 0, "cluster_label": "A", "total_pnl_yen_100": 100.0},
                {"cluster_id": 1, "cluster_label": "B", "total_pnl_yen_100": -50.0},
            ]
        )
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()

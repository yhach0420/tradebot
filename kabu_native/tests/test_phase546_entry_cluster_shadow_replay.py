"""Phase546 entry cluster shadow replay unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase546_entry_cluster_shadow_replay import (  # noqa: E402
    PHASE546_VERDICT,
    VARIANTS,
    VariantSpec,
    _is_bonus,
    _is_rejected,
)


class TestPhase546EntryClusterShadowReplay(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE546_VERDICT, "phase546_entry_cluster_shadow_replay_done")

    def test_variant_count(self) -> None:
        self.assertEqual(len(VARIANTS), 9)

    def test_cluster5_reject(self) -> None:
        spec = next(v for v in VARIANTS if v.variant_id == "V1")
        row = {"cluster_id": 5, "subcluster_id": "", "new_subcluster_id": ""}
        self.assertTrue(_is_rejected(row, spec))
        self.assertFalse(_is_rejected({"cluster_id": 1}, spec))

    def test_balanced_reject_csub(self) -> None:
        spec = next(v for v in VARIANTS if v.variant_id == "V6")
        self.assertTrue(_is_rejected({"cluster_id": 3, "new_subcluster_id": 0}, spec))
        self.assertTrue(_is_rejected({"cluster_id": 4, "new_subcluster_id": 3}, spec))

    def test_bonus_only(self) -> None:
        spec = VariantSpec("V7", "Bonus", bonus_cluster=frozenset({1}), bonus_csub=frozenset({7}))
        self.assertTrue(_is_bonus({"cluster_id": 1}, spec))
        self.assertTrue(_is_bonus({"new_subcluster_id": 7}, spec))
        self.assertFalse(_is_rejected({"cluster_id": 5}, spec))


if __name__ == "__main__":
    unittest.main()

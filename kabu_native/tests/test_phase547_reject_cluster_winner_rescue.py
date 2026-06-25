"""Phase547 reject cluster winner rescue unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase547_reject_cluster_winner_rescue import (  # noqa: E402
    PHASE547_VERDICT,
    V6_SPEC,
    _build_exception_fns,
    _reject_reason,
)
from research.phase546_entry_cluster_shadow_replay import _is_rejected  # noqa: E402


class TestPhase547RejectClusterWinnerRescue(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE547_VERDICT, "phase547_reject_cluster_winner_rescue_done")

    def test_v6_reject_cluster5(self) -> None:
        row = {"cluster_id": 5, "new_subcluster_id": ""}
        self.assertTrue(_is_rejected(row, V6_SPEC))
        self.assertEqual(_reject_reason(row), "cluster5")

    def test_v6_reject_csub(self) -> None:
        row = {"cluster_id": 3, "new_subcluster_id": 0}
        self.assertTrue(_is_rejected(row, V6_SPEC))
        self.assertIn("csub0", _reject_reason(row))

    def test_exception_e1(self) -> None:
        fns = _build_exception_fns({"liquidity_burst_p75": 0.1, "price_acceleration_p75": 0.1})
        self.assertTrue(fns["E1"][2]({"board_imbalance": 0.65}))
        self.assertFalse(fns["E1"][2]({"board_imbalance": 0.50}))


if __name__ == "__main__":
    unittest.main()

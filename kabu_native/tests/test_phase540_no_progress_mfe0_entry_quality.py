"""Phase540: NoProgress / MFE0 entry quality study unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase540_no_progress_mfe0_entry_quality import (  # noqa: E402
    GUARD_IDS,
    PHASE540_VERDICT,
    _guard_allows,
    _is_mfe0,
    _is_mfe0_relaxed,
    _is_no_progress,
    _mfe_bucket,
    _no_progress_subgroup,
    _resolved_exit_reason,
)


class TestPhase540NoProgressMfe0(unittest.TestCase):
    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE540_VERDICT, "phase540_no_progress_mfe0_entry_quality_done")

    def test_guard_ids(self) -> None:
        self.assertEqual(GUARD_IDS[0], "A_baseline")
        self.assertIn("G10_spread_trend_no_plb", GUARD_IDS)
        self.assertEqual(len(GUARD_IDS), 13)

    def test_mfe0_definitions(self) -> None:
        self.assertTrue(_is_mfe0({"peak_mfe_pct": 0.0}))
        self.assertFalse(_is_mfe0({"peak_mfe_pct": 0.05}))
        self.assertTrue(_is_mfe0_relaxed({"peak_mfe_pct": 0.01}))
        self.assertFalse(_is_mfe0_relaxed({"peak_mfe_pct": 0.02}))

    def test_no_progress_exit_reason(self) -> None:
        row = {"exit_reason": "no_progress_exit", "structural_exit_reason": "no_progress_exit"}
        self.assertTrue(_is_no_progress(row))
        self.assertEqual(_resolved_exit_reason(row), "no_progress_exit")

    def test_mfe_bucket_winner(self) -> None:
        self.assertEqual(_mfe_bucket({"pnl_yen_100": 100, "peak_mfe_pct": 0.0}), "D_winner")

    def test_no_progress_subgroup(self) -> None:
        self.assertEqual(
            _no_progress_subgroup({"exit_reason": "no_progress_exit", "peak_mfe_pct": 0.0}),
            "mfe0_no_progress",
        )
        self.assertEqual(
            _no_progress_subgroup({"exit_reason": "no_progress_exit", "peak_mfe_pct": 0.1}),
            "low_mfe_no_progress",
        )

    def test_g1_spread_guard(self) -> None:
        self.assertTrue(_guard_allows("G1_spread_le40", {"spread_bps": 35.0}, best_rules={}))
        self.assertFalse(_guard_allows("G1_spread_le40", {"spread_bps": 55.0}, best_rules={}))

    def test_g5_trend_guard(self) -> None:
        self.assertTrue(_guard_allows("G5_trend_not_down", {"trend_direction": "up"}, best_rules={}))
        self.assertFalse(_guard_allows("G5_trend_not_down", {"trend_direction": "down"}, best_rules={}))

    def test_baseline_allows_all(self) -> None:
        self.assertTrue(_guard_allows("A_baseline", {}, best_rules={}))


if __name__ == "__main__":
    unittest.main()

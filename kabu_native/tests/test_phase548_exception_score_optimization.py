"""Phase548 exception score optimization unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase548_exception_score_optimization import (  # noqa: E402
    PHASE548_VERDICT,
    SCORE_COMPONENTS,
    _exception_score,
    _score_components,
)


class TestPhase548ExceptionScoreOptimization(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE548_VERDICT, "phase548_exception_score_optimization_done")

    def test_max_score_nine(self) -> None:
        self.assertEqual(sum(c[2] for c in SCORE_COMPONENTS), 9)

    def test_score_high_liquidity(self) -> None:
        thr = {
            "liquidity_burst_p75": 0.05,
            "vwap_recovery_min_median": 20.0,
            "update_count_median": 1.0,
            "relative_volume_p75": 1.1,
        }
        row = {
            "liquidity_burst": 0.1,
            "vwap_recovery_min": 10.0,
            "update_count_before_entry": 2.0,
            "relative_volume": 1.2,
            "day_return_rank": 15.0,
            "board_imbalance": 0.65,
            "minutes_from_open": 30.0,
            "entry_rise_5min_pct": 0.5,
        }
        self.assertGreaterEqual(_exception_score(row, thr), 5)
        comps = _score_components(row, thr)
        self.assertEqual(comps["liquidity_burst_high"], 2)


if __name__ == "__main__":
    unittest.main()

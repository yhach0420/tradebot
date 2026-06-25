"""Phase536: OR universe sensitivity unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase536_or_universe_sensitivity import (  # noqa: E402
    PHASE536_VERDICT,
    STRATEGIES,
    UNIVERSE_SPECS,
    _filter_candidates_universe,
    _global_dynamic_rank,
)


class TestPhase536Universe(unittest.TestCase):
    def test_universe_and_strategy_counts(self) -> None:
        self.assertEqual(len(UNIVERSE_SPECS), 4)
        self.assertEqual(len(STRATEGIES), 4)

    def test_filter_candidates_universe(self) -> None:
        cands = [
            {"symbol": "7203.T", "day": "20260602"},
            {"symbol": "9984.T", "day": "20260602"},
        ]
        univ = {"20260602": {"7203"}}
        out = _filter_candidates_universe(cands, univ)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "7203.T")

    def test_global_dynamic_rank(self) -> None:
        pool = [
            {"symbol": "7203.T"},
            {"symbol": "7203.T"},
            {"symbol": "9984.T"},
        ]
        rank = _global_dynamic_rank(pool, {"1111"})
        self.assertEqual(rank["7203"], 1)
        self.assertEqual(rank["9984"], 2)

    def test_verdict(self) -> None:
        self.assertEqual(PHASE536_VERDICT, "phase536_or_universe_sensitivity_done")


if __name__ == "__main__":
    unittest.main()

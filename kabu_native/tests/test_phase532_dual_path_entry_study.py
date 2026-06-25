"""Phase532: dual-path entry study unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase532_dual_path_entry_study import (  # noqa: E402
    PHASE532_VERDICT,
    S4,
    S5,
    STRATEGIES,
    _passes_f6,
    _passes_g9,
)


class TestPhase532DualPath(unittest.TestCase):
    def test_strategies_count(self) -> None:
        self.assertEqual(len(STRATEGIES), 6)
        self.assertIn(S4, STRATEGIES)
        self.assertIn(S5, STRATEGIES)

    def test_g9_predicate(self) -> None:
        self.assertTrue(_passes_g9({"spread": 40, "update_count_before_entry": 3}))
        self.assertFalse(_passes_g9({"spread": 60, "update_count_before_entry": 3}))

    def test_f6_predicate(self) -> None:
        self.assertTrue(_passes_f6({"minutes_from_open": 120}))
        self.assertFalse(_passes_f6({"minutes_from_open": 200}))

    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE532_VERDICT, "phase532_dual_path_entry_study_done")


if __name__ == "__main__":
    unittest.main()

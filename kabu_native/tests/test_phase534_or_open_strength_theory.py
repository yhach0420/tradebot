"""Phase534: OR open strength theory unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase534_or_open_strength_theory import (  # noqa: E402
    PHASE534_VERDICT,
    _cap_study_design_rows,
    _filter_allows,
    _universe_study_design_rows,
)


class TestPhase534OpenStrength(unittest.TestCase):
    def test_os9_open_strength_proxy(self) -> None:
        row = {
            "minutes_from_open": 60,
            "day_return_rank": 5,
            "vwap_distance": 1.5,
            "volume_percentile": 90,
        }
        self.assertTrue(_filter_allows("OS9_open_strength_proxy", row, speed_p75=0.03))

    def test_cap_design_count(self) -> None:
        self.assertEqual(len(_cap_study_design_rows()), 7)

    def test_universe_design_count(self) -> None:
        self.assertEqual(len(_universe_study_design_rows()), 4)

    def test_verdict(self) -> None:
        self.assertEqual(PHASE534_VERDICT, "phase534_or_open_strength_theory_done")


if __name__ == "__main__":
    unittest.main()

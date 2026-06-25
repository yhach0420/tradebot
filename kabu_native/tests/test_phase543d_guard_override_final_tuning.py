"""Phase543D override final tuning unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase543d_guard_override_final_tuning import (  # noqa: E402
    OVERRIDE_DEFS,
    PHASE543D_VERDICT,
    _day_leader_proxy,
    _override_allows,
    _open_strength_proxy,
)


class TestPhase543DOverrideFinalTuning(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE543D_VERDICT, "phase543d_guard_override_final_tuning_done")

    def test_ten_overrides(self) -> None:
        self.assertEqual(len(OVERRIDE_DEFS), 10)

    def test_o1_board(self) -> None:
        self.assertTrue(_override_allows("O1", {"board_imbalance": 0.65}))
        self.assertFalse(_override_allows("O1", {"board_imbalance": 0.55}))

    def test_o2_volume(self) -> None:
        row = {"board_imbalance": 0.56, "volume_percentile": 85.0}
        self.assertTrue(_override_allows("O2", row))
        self.assertFalse(_override_allows("O2", {**row, "volume_percentile": 70.0}))

    def test_day_leader_proxy(self) -> None:
        self.assertTrue(_day_leader_proxy({"day_return_rank": 15.0, "volume_percentile": 75.0}))
        self.assertFalse(_day_leader_proxy({"day_return_rank": 25.0, "volume_percentile": 75.0}))

    def test_open_strength_proxy(self) -> None:
        self.assertTrue(
            _open_strength_proxy({"minutes_from_open": 60.0, "entry_rise_5min_pct": 0.5})
        )


if __name__ == "__main__":
    unittest.main()

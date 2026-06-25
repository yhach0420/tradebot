"""Phase543A: lost winner override design unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase543_guard_v2_lost_winner_override import (  # noqa: E402
    GUARD_SPECS,
    OVERRIDE_IDS,
    PHASE543_VERDICT,
    _guard_blocks,
    _override_allows,
    _strategy_allows,
)


class TestPhase543LostWinnerOverride(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE543_VERDICT, "phase543_guard_v2_lost_winner_override_done")

    def test_guard_specs(self) -> None:
        self.assertIn("G_A", GUARD_SPECS)
        self.assertEqual(len(OVERRIDE_IDS), 12)

    def test_override_o1(self) -> None:
        self.assertTrue(_override_allows("O1_board_imbalance", {"board_imbalance": 0.65}, momentum_p75=0.2))
        self.assertFalse(_override_allows("O1_board_imbalance", {"board_imbalance": 0.5}, momentum_p75=0.2))

    def test_strategy_guard_or_override(self) -> None:
        spec = GUARD_SPECS["G_B"]
        blocked_feats = {"adx14": 40.0, "five_min_position": 30.0, "board_imbalance": 0.7}
        self.assertTrue(_guard_blocks(blocked_feats, spec))
        self.assertTrue(
            _strategy_allows(blocked_feats, spec, "O1_board_imbalance", momentum_p75=0.2)
        )

    def test_o12_day_leader_proxy(self) -> None:
        ok = {"day_return_rank": 15.0, "volume_percentile": 75.0}
        self.assertTrue(_override_allows("O12_day_leader_proxy", ok, momentum_p75=0.1))
        self.assertFalse(
            _override_allows("O12_day_leader_proxy", {"day_return_rank": 15.0, "volume_percentile": 50.0}, momentum_p75=0.1)
        )


if __name__ == "__main__":
    unittest.main()

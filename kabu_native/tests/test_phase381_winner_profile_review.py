"""Phase381 winner profile tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase381_winner_profile_review import (  # noqa: E402
    classify_winner_type,
    compare_winning_vs_low_mfe,
    feature_stats,
    is_winning,
    top_set_composition,
    winner_profile_score,
)


class TestPhase381WinnerProfile(unittest.TestCase):
    def _trades(self) -> list[dict]:
        return [
            {
                "pnl_yen_100": 5000.0,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 1.2,
                "hold_seconds": 600.0,
                "universe_group": "dynamic40",
                "dynamic40_rank_bucket": "rank_31_40",
                "session_kind": "pm",
                "entry_imbalance_percentile": 20.0,
                "entry_momentum_score": 0.4,
                "board_dynamic_tier": "board_low",
                "price_range_position": 0.5,
            },
            {
                "pnl_yen_100": -1000.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.1,
                "hold_seconds": 100.0,
                "entry_momentum_score": 0.1,
                "entry_imbalance_percentile": 80.0,
            },
            {
                "pnl_yen_100": 2000.0,
                "exit_reason_canonical": "overlap_replaced",
                "peak_mfe_pct": 0.5,
                "hold_seconds": 120.0,
                "universe_group": "core10",
                "session_kind": "am",
            },
        ]

    def test_is_winning(self) -> None:
        self.assertTrue(is_winning(self._trades()[0]))

    def test_classify_winner_type(self) -> None:
        self.assertEqual(classify_winner_type(self._trades()[0]), "trend_follow_winner")
        self.assertEqual(classify_winner_type(self._trades()[2]), "overlap_winner")

    def test_feature_stats(self) -> None:
        wins = [t for t in self._trades() if is_winning(t)]
        rows = feature_stats(wins, cohort="winning")
        self.assertTrue(any(r["feature"] == "entry_momentum_score" for r in rows))

    def test_compare_winning_vs_low_mfe(self) -> None:
        trades = self._trades()
        rows = compare_winning_vs_low_mfe(
            [t for t in trades if is_winning(t)],
            [t for t in trades if not is_winning(t)],
        )
        self.assertTrue(rows)

    def test_top_set_composition(self) -> None:
        wins = [t for t in self._trades() if is_winning(t)]
        comp = top_set_composition(wins, 2)
        self.assertEqual(comp["trade_count"], 2)

    def test_winner_profile_score(self) -> None:
        self.assertGreaterEqual(winner_profile_score(self._trades()[0]), 4)


if __name__ == "__main__":
    unittest.main()

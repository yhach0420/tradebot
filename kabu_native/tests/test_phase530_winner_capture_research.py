"""Phase530: winner capture research unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase530_winner_capture_research import (  # noqa: E402
    PHASE530_VERDICT,
    STRATEGIES,
    _capture_for_day,
    _mfe_hit_rows,
    _pbv2_missed_rows,
    _winner_capture_score,
)


class TestPhase530WinnerCapture(unittest.TestCase):
    def test_strategies_tuple(self) -> None:
        self.assertIn("BASELINE_RUNTIME", STRATEGIES)
        self.assertIn("O_R003_OR", STRATEGIES)
        self.assertIn("G3_G4", STRATEGIES)

    def test_mfe_hit_rows(self) -> None:
        trades = {
            "BASELINE_RUNTIME": [
                {"mfe_pct": 0.3},
                {"mfe_pct": 1.2},
                {"mfe_pct": 5.5},
            ]
        }
        rows = _mfe_hit_rows(trades)
        hit_05 = next(r for r in rows if r["mfe_threshold_pct"] == 0.5)
        hit_5 = next(r for r in rows if r["mfe_threshold_pct"] == 5.0)
        self.assertEqual(hit_05["hit_count"], 2)
        self.assertEqual(hit_5["hit_count"], 1)

    def test_capture_for_day(self) -> None:
        row = _capture_for_day(
            day="20260624",
            universe_type="day_return",
            top_n=10,
            strategy_id="BASELINE_RUNTIME",
            day_trades=[
                {"symbol": "5074.T", "day": "20260624", "mfe_pct": 1.5},
                {"symbol": "6976.T", "day": "20260624", "mfe_pct": 0.2},
            ],
            universe_syms={"5074", "6976", "9999"},
        )
        self.assertEqual(row["capture_count"], 2)
        self.assertEqual(row["effective_capture_count"], 1)
        self.assertEqual(row["strong_capture_count"], 0)

    def test_pbv2_missed_classification(self) -> None:
        price_idx = {
            ("5074.T", "20260624"): [(0, 100.0), (1, 110.0)],
            ("6976.T", "20260624"): [(0, 200.0), (1, 210.0)],
        }
        trades = {
            "BASELINE_RUNTIME": [{"symbol": "5074.T", "day": "20260624", "mfe_pct": 1.0}],
            "O_R003_OR": [{"symbol": "6976.T", "day": "20260624", "mfe_pct": 2.0}],
            "G3_G4": [],
        }
        rows = _pbv2_missed_rows(
            days=["20260624"],
            price_idx=price_idx,
            universe=["5074", "6976"],
            trades_by_strategy=trades,
            top_n=10,
        )
        classes = {r["classification"] for r in rows}
        self.assertTrue(classes)

    def test_winner_capture_score_weights(self) -> None:
        detail = [
            {
                "strategy_id": "X",
                "universe_type": "day_return",
                "top_n": 10,
                "capture_rate": 0.5,
                "effective_capture_rate": 0.4,
                "strong_capture_rate": 0.2,
            }
        ]
        score = _winner_capture_score(detail, "X")
        self.assertAlmostEqual(score, 0.2 * 0.5 + 0.3 * 0.4 + 0.5 * 0.2, places=4)

    def test_verdict_constant(self) -> None:
        self.assertEqual(PHASE530_VERDICT, "phase530_winner_capture_research_done")


if __name__ == "__main__":
    unittest.main()

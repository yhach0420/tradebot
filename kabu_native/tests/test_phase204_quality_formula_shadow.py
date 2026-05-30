import unittest
from pathlib import Path

from small_paper.quality_formula_shadow import (
    SHADOW_FIELD_KEYS,
    assign_session_quality_ranks,
    compute_shadow_quality_score,
    finalize_session_quality_shadow,
)


class TestPhase204QualityFormulaShadow(unittest.TestCase):
    def test_event_fields_include_shadow_columns(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(f'"{key}"', pilot_src)

    def test_shadow_score_persistence_plus_trading_value(self) -> None:
        trade = {
            "max_continuation_duration": 14.0,
            "trading_value": 1e10,
        }
        score = compute_shadow_quality_score(trade)
        self.assertAlmostEqual(score, 0.8333, places=4)

    def test_session_ranks_assigned(self) -> None:
        rows = [
            {
                "symbol": "A.T",
                "entry_time": "t1",
                "continuation_quality_score": 0.9,
                "shadow_quality_score": 0.2,
            },
            {
                "symbol": "B.T",
                "entry_time": "t2",
                "continuation_quality_score": 0.5,
                "shadow_quality_score": 0.8,
            },
        ]
        events = [
            {"event_type": "accepted", "symbol": "A.T", "entry_time": "t1"},
            {"event_type": "accepted", "symbol": "B.T", "entry_time": "t2"},
            {"event_type": "observer_exit", "symbol": "A.T", "entry_time": "t1", "pnl_pct": -1.0},
            {"event_type": "observer_exit", "symbol": "B.T", "entry_time": "t2", "pnl_pct": 1.0},
        ]
        summary = finalize_session_quality_shadow(rows, events)
        self.assertEqual(rows[0]["current_quality_rank"], 1)
        self.assertEqual(rows[1]["shadow_quality_rank"], 1)
        self.assertIn("current_quality_top20_pf", summary)
        self.assertIn("shadow_quality_top20_pf", summary)
        self.assertTrue(summary["quality_formula_shadow_enabled"])


if __name__ == "__main__":
    unittest.main()

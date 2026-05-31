import unittest
from pathlib import Path

from small_paper.entry_expectancy_score_shadow import (
    SCORE_GE5_THRESHOLD,
    SCORE_GE6_THRESHOLD,
    SCORE_POINTS,
    SHADOW_FIELD_KEYS,
    SUMMARY_FIELD_KEYS,
    TERTILE_CUTOFFS,
    EntryExpectancyScoreCounters,
    compute_entry_expectancy_score_fields,
    finalize_session_entry_expectancy_score,
)


class TestPhase230EntryExpectancyScoreShadow(unittest.TestCase):
    def test_event_fields_in_pilot_runner(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS:
            self.assertIn(f'"{key}"', pilot_src)

    def test_score_map_matches_phase229(self) -> None:
        self.assertEqual(SCORE_POINTS["HBRecent:no"], 2)
        self.assertEqual(SCORE_POINTS["RollingMAE:mid"], 2)
        self.assertEqual(SCORE_POINTS["TV:mid"], 1)
        self.assertAlmostEqual(TERTILE_CUTOFFS["TV"]["p33"], 12851022500.0)

    def test_max_score_example(self) -> None:
        trade = {
            "trading_value": 3e10,
            "rolling_mae_pct": -0.0003,
            "entry_high_break_recent": False,
            "max_continuation_duration": 500.0,
            "momentum_continuation_score": 0.20,
            "entry_order_book_imbalance": 0.50,
            "current_price": 5000.0,
        }
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score"], 10)
        self.assertTrue(fields["entry_expectancy_score_ge5_flag"])
        self.assertTrue(fields["entry_expectancy_score_ge6_flag"])

    def test_score_below_threshold(self) -> None:
        trade = {
            "trading_value": 1e9,
            "rolling_mae_pct": -0.01,
            "entry_high_break_recent": True,
            "max_continuation_duration": 10.0,
            "momentum_continuation_score": 0.35,
            "entry_order_book_imbalance": 0.40,
            "current_price": 1000.0,
        }
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertLess(fields["entry_expectancy_score"], SCORE_GE5_THRESHOLD)
        self.assertFalse(fields["entry_expectancy_score_ge5_flag"])
        self.assertFalse(fields["entry_expectancy_score_ge6_flag"])

    def test_counters_and_finalize(self) -> None:
        counters = EntryExpectancyScoreCounters()
        acc = {
            "entry_expectancy_score": 6,
            "entry_expectancy_score_ge5_flag": True,
            "entry_expectancy_score_ge6_flag": True,
        }
        counters.record_accept(acc)
        counters.record_exit(
            {
                **acc,
                "pnl_pct": 1.5,
                "exit_reason": "trailing_mfe_exit",
            }
        )
        summary = counters.summary_fields()
        for key in SUMMARY_FIELD_KEYS:
            self.assertIn(key, summary)
        self.assertEqual(summary["score5_count"], 1)
        self.assertEqual(summary["score6_count"], 1)
        self.assertEqual(summary["score5_pnl"], 1.5)

        rows = [
            {
                "symbol": "A.T",
                "entry_time": "t1",
                "entry_expectancy_score_ge5_flag": True,
                "entry_expectancy_score_ge6_flag": False,
            }
        ]
        events = [
            {
                "event_type": "observer_exit",
                "symbol": "A.T",
                "entry_time": "t1",
                "pnl_pct": 0.8,
                "exit_reason": "trailing_mfe_exit",
            }
        ]
        fin = finalize_session_entry_expectancy_score(rows, events)
        self.assertEqual(fin["score5_count"], 1)
        self.assertEqual(fin["score5_pnl"], 0.8)


if __name__ == "__main__":
    unittest.main()

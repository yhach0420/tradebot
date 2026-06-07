import unittest
from pathlib import Path

from small_paper.entry_expectancy_score_shadow import (
    ALL_SHADOW_FIELD_KEYS,
    ENTRY_SCORE_V2_GATE_MIN,
    REQUIRED_V2_TOKENS,
    SCORE_GE5_THRESHOLD,
    SCORE_POINTS,
    SCORE_POINTS_V2,
    SHADOW_FIELD_KEYS_V2,
    SUMMARY_FIELD_KEYS_V2,
    EntryExpectancyScoreCounters,
    active_score_tokens_v2,
    compute_entry_expectancy_score_fields,
    enrich_exit_entry_expectancy_fields,
    finalize_session_entry_expectancy_score,
    momentum_low_required_for_v2,
)


class TestPhase237EntryExpectancyScoreV2Shadow(unittest.TestCase):
    def test_event_fields_in_pilot_runner(self) -> None:
        pilot_src = (
            Path(__file__).resolve().parents[1] / "src" / "small_paper" / "pilot_runner.py"
        ).read_text(encoding="utf-8")
        for key in SHADOW_FIELD_KEYS_V2:
            self.assertIn(f'"{key}"', pilot_src)

    def test_v2_phase314_tokens(self) -> None:
        for removed in (
            "RollingMAE:mid",
            "Duration:high",
            "Price:high",
            "TV:mid",
            "HBRecent:no",
        ):
            self.assertNotIn(removed, SCORE_POINTS_V2)
        self.assertEqual(
            SCORE_POINTS_V2,
            {
                "Momentum:low": 2,
                "Board:mid": 1,
            },
        )
        self.assertEqual(sum(SCORE_POINTS_V2.values()), ENTRY_SCORE_V2_GATE_MIN)
        self.assertEqual(REQUIRED_V2_TOKENS, frozenset({"Momentum:low"}))

    def test_v2_max_score_momentum_and_board(self) -> None:
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
        self.assertEqual(fields["entry_expectancy_score"], 8)
        self.assertEqual(fields["entry_expectancy_score_v2"], 3)
        self.assertFalse(fields["entry_expectancy_score_v2_ge5_flag"])
        self.assertFalse(fields["entry_expectancy_score_v2_ge6_flag"])
        self.assertEqual(active_score_tokens_v2(trade), ["Momentum:low", "Board:mid"])
        self.assertTrue(momentum_low_required_for_v2(trade))

    def test_v2_momentum_only_score_two(self) -> None:
        trade = {
            "momentum_continuation_score": 0.20,
            "entry_order_book_imbalance": 0.40,
        }
        fields = compute_entry_expectancy_score_fields(trade=trade)
        self.assertEqual(fields["entry_expectancy_score_v2"], 2)
        self.assertEqual(active_score_tokens_v2(trade), ["Momentum:low"])

    def test_all_shadow_keys_present(self) -> None:
        trade = {"trading_value": 1e9, "current_price": 1000.0}
        fields = compute_entry_expectancy_score_fields(trade=trade)
        for key in ALL_SHADOW_FIELD_KEYS:
            self.assertIn(key, fields)

    def test_exit_enrich_and_counters(self) -> None:
        acc = compute_entry_expectancy_score_fields(
            trade={
                "momentum_continuation_score": 0.20,
                "entry_order_book_imbalance": 0.50,
            }
        )
        exit_row = enrich_exit_entry_expectancy_fields(
            acc,
            pnl_pct=1.2,
            exit_reason="trailing_mfe_exit",
        )
        for key in SHADOW_FIELD_KEYS_V2:
            self.assertIn(key, exit_row)

        counters = EntryExpectancyScoreCounters()
        counters.record_accept(acc)
        counters.record_exit({**exit_row, **acc})
        summary = counters.summary_fields()
        for key in SUMMARY_FIELD_KEYS_V2:
            self.assertIn(key, summary)
        self.assertTrue(summary["phase237_entry_expectancy_score_v2_shadow"])
        self.assertEqual(summary["score5_v2_count"], 0)
        self.assertEqual(summary["score6_v2_count"], 0)

        fin = finalize_session_entry_expectancy_score(
            [
                {
                    "symbol": "A.T",
                    "entry_time": "t1",
                    **acc,
                }
            ],
            [
                {
                    "event_type": "observer_exit",
                    "symbol": "A.T",
                    "entry_time": "t1",
                    "pnl_pct": 0.5,
                    "exit_reason": "trailing_mfe_exit",
                }
            ],
        )
        self.assertIn("score5_v2_pf", fin)
        self.assertIn("score6_v2_pf", fin)


if __name__ == "__main__":
    unittest.main()

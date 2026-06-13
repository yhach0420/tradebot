import unittest

from research.phase345_board_failure_forensic import (
    Phase345ForensicReview,
    is_profit_take_miss,
    is_stop_hit_saved,
)
from small_paper.board_failure_forensic_pack import classify_forensic_trade


class TestPhase345BoardFailureForensic(unittest.TestCase):
    def test_classify_false_positive_on_rebound(self) -> None:
        cls = classify_forensic_trade(
            shadow_triggered=True,
            pnl_difference_yen=-5000.0,
            actual_pnl_yen=9000.0,
            actual_exit_reason="trailing_mfe_exit",
            post_shadow_max_up_pct=0.8,
            post_shadow_max_down_pct=0.0,
        )
        self.assertEqual(cls, "B_false_positive")

    def test_classify_correct_cut(self) -> None:
        cls = classify_forensic_trade(
            shadow_triggered=True,
            pnl_difference_yen=3000.0,
            actual_pnl_yen=-5500.0,
            actual_exit_reason="stop_hit",
            post_shadow_max_up_pct=0.05,
            post_shadow_max_down_pct=-0.2,
        )
        self.assertEqual(cls, "A_correct_cut")

    def test_profit_take_miss_detector(self) -> None:
        row = {
            "actual_pnl_yen_100": 5000.0,
            "pnl_difference_yen_100": -3000.0,
            "shadow_triggered": True,
        }
        self.assertTrue(is_profit_take_miss(row))

    def test_stop_hit_saved_detector(self) -> None:
        row = {
            "actual_exit_reason": "stop_hit",
            "pnl_difference_yen_100": 2000.0,
            "shadow_triggered": True,
            "actual_pnl_yen_100": -3000.0,
        }
        self.assertTrue(is_stop_hit_saved(row))

    def test_review_summary(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            review = Phase345ForensicReview(reports_dir=Path(tmp))
            review.ingest_forensic_rows(
                [
                    {
                        "day_key": "20260528",
                        "symbol": "9984.T",
                        "position_id": "p1",
                        "shadow_triggered": True,
                        "actual_pnl_yen_100": 8000.0,
                        "pnl_difference_yen_100": -10000.0,
                        "actual_exit_reason": "trailing_mfe_exit",
                        "post_shadow_max_up_pct": 0.5,
                        "peak_mfe_pct": 0.15,
                        "shadow_board_imbalance_delta": -0.1,
                        "forensic_class": "B_false_positive",
                    },
                    {
                        "day_key": "20260528",
                        "symbol": "6526.T",
                        "position_id": "p2",
                        "shadow_triggered": True,
                        "actual_pnl_yen_100": -5000.0,
                        "pnl_difference_yen_100": 2000.0,
                        "actual_exit_reason": "stop_hit",
                        "post_shadow_max_up_pct": 0.05,
                        "post_shadow_max_down_pct": -0.15,
                        "peak_mfe_pct": 0.1,
                        "shadow_board_imbalance_delta": -0.12,
                        "forensic_class": "A_correct_cut",
                    },
                ]
            )
            summary = review.build_summary()
            self.assertEqual(summary["positions_analyzed"], 2)
            self.assertIn("conclusions", summary)


if __name__ == "__main__":
    unittest.main()

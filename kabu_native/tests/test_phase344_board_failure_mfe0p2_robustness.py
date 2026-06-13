import unittest
from pathlib import Path

from research.phase344_board_failure_mfe0p2_robustness import (
    VARIANT_ID,
    Phase344BoardFailureMfe0p2RobustnessAggregator,
    _load_phase343_variant_baseline,
)
from small_paper.board_failure_exit_tuning import (
    VARIANT_MFE_LT_0P2_CONFIRM5,
    phase344_mfe_lt_0p2_confirm5_variant,
)


class TestPhase344BoardFailureMfe0p2Robustness(unittest.TestCase):
    def test_variant_factory(self) -> None:
        v = phase344_mfe_lt_0p2_confirm5_variant()
        self.assertEqual(v.variant_id, VARIANT_MFE_LT_0P2_CONFIRM5)
        self.assertEqual(v.max_mfe_pct, 0.2)
        self.assertEqual(v.confirm_ticks, 5)

    def test_aggregator_filters_variant(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase344BoardFailureMfe0p2RobustnessAggregator(reports_dir=Path(tmp))
            rows = [
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "variant_id": VARIANT_ID,
                    "shadow_pnl_yen_100": -200.0,
                    "actual_pnl_yen_100": -500.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 300.0,
                    "no_candidate_trigger": False,
                    "peak_mfe_pct": 0.1,
                },
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "variant_id": "mfe_lt_0p3_confirm3",
                    "shadow_pnl_yen_100": 0.0,
                    "actual_pnl_yen_100": -500.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 0.0,
                    "no_candidate_trigger": True,
                    "peak_mfe_pct": 0.1,
                },
            ]
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260521"},
                trade_rows=rows,
                push_rows=1000,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["positions_evaluated"], 1)
            self.assertEqual(summary["variant_metrics"]["trigger_count"], 1)

    def test_robustness_verdict_structure(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase344BoardFailureMfe0p2RobustnessAggregator(reports_dir=Path(tmp))
            metrics = {
                "delta_yen": 5000.0,
                "profit_factor": 0.6,
                "profit_take_miss_yen_100": -100.0,
                "stop_hit_reduction_count": 2,
                "improved_session_count": 2,
                "worsened_session_count": 1,
                "symbol_concentration": False,
                "top_symbol_delta_share": 0.3,
            }
            verdict = agg.robustness_verdict(metrics)
            self.assertIn("robustness_pass", verdict)
            self.assertIn("checks", verdict)

    def test_phase343_baseline_fallback(self) -> None:
        import tempfile

        baseline = _load_phase343_variant_baseline(Path(tempfile.mkdtemp()))
        self.assertEqual(baseline["label"], "phase343_mfe_lt_0p2_confirm5")


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
import tempfile

from research.phase340_vwap_dev_finetune_evaluation import (
    Phase340IncrementalAggregator,
    adoption_assessment,
    tradeoff_score,
)
from small_paper.vwap_assisted_loss_tuning import default_phase340_variants


class TestPhase340VwapDevFinetune(unittest.TestCase):
    def test_default_variants(self) -> None:
        variants = default_phase340_variants()
        ids = [v.variant_id for v in variants]
        self.assertIn("baseline", ids)
        self.assertIn("vwap_dev_0p5pct", ids)
        self.assertEqual(len(variants), 6)

    def test_tradeoff_prefers_stop_reduction(self) -> None:
        self.assertGreater(tradeoff_score(-200.0, 3), tradeoff_score(-200.0, 1))
        self.assertGreater(tradeoff_score(-100.0, 2), tradeoff_score(-800.0, 2))

    def test_adoption_assessment(self) -> None:
        metrics = {
            "delta_yen": 500.0,
            "profit_factor": 1.2,
            "stop_hit_reduction_count": 2,
            "profit_take_miss_yen_100": -100.0,
            "improved_session_count": 2,
            "worsened_session_count": 1,
            "symbol_concentration": False,
        }
        out = adoption_assessment(metrics, actual_total=-1000.0, actual_pf=0.8)
        self.assertTrue(out["adopt_ready"])

    def test_incremental_aggregator_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase340IncrementalAggregator(reports_dir=Path(tmp))
            rows = []
            for vid in ["baseline", "vwap_dev_0p5pct"]:
                rows.append(
                    {
                        "position_id": "P1",
                        "variant_id": vid,
                        "symbol": "9984.T",
                        "shadow_pnl_yen_100": 200.0 if vid == "vwap_dev_0p5pct" else -500.0,
                        "actual_pnl_yen_100": -500.0,
                        "actual_exit_reason": "stop_hit",
                        "candidate_vs_actual_delta_yen": 700.0 if vid == "vwap_dev_0p5pct" else 0.0,
                        "no_candidate_trigger": vid == "baseline",
                    }
                )
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260521"},
                trade_rows=rows,
                push_rows=1000,
                runtime_sec=10.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["positions_evaluated"], 1)
            v05 = summary["variants"]["vwap_dev_0p5pct"]
            self.assertEqual(v05["stop_hit_reduction_count"], 1)
            self.assertEqual(v05["improved_session_count"], 1)
            paths = agg.finalize_outputs()
            self.assertTrue(Path(paths["summary"]).is_file())


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path

from research.phase347_board_failure_cooldown_finetune import (
    PHASE346_CD60_VARIANT,
    Phase347BoardFailureCooldownFinetuneAggregator,
    _load_phase346_cd60_baseline,
)
from small_paper.board_failure_false_positive_guard import (
    BASE_VARIANT_ID,
    default_phase347_variants,
)


class TestPhase347CooldownFinetune(unittest.TestCase):
    def test_default_variants(self) -> None:
        variants = default_phase347_variants()
        ids = [v.variant_id for v in variants]
        self.assertEqual(len(variants), 4)
        self.assertEqual(ids, [f"{BASE_VARIANT_ID}_cd{s}" for s in (45, 60, 75, 90)])
        for v in variants:
            self.assertEqual(v.confirm_ticks, 5)
            self.assertEqual(v.max_mfe_pct, 0.2)

    def test_aggregator_ingest(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase347BoardFailureCooldownFinetuneAggregator(reports_dir=Path(tmp))
            rows = [
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "variant_id": f"{BASE_VARIANT_ID}_cd60",
                    "shadow_pnl_yen_100": 1000.0,
                    "actual_pnl_yen_100": -500.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 1500.0,
                    "no_candidate_trigger": False,
                    "forensic_class": "A_correct_cut",
                    "peak_mfe_pct": 0.1,
                }
            ]
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260528"},
                trade_rows=rows,
                push_rows=100,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            met = summary["variants"][f"{BASE_VARIANT_ID}_cd60"]
            self.assertEqual(met["stop_hit_reduction_count"], 1)
            self.assertEqual(met["session_delta_yen_20260528"], 1500.0)

    def test_finetune_pass_assessment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase347BoardFailureCooldownFinetuneAggregator(reports_dir=Path(tmp))
            metrics = {
                "delta_yen": 5000.0,
                "profit_factor": 0.9,
                "profit_take_miss_yen_100": -1000.0,
                "stop_hit_reduction_count": 5,
                "session_delta_yen_20260528": 0.0,
                "improved_session_count": 3,
                "worsened_session_count": 1,
                "symbol_concentration": False,
            }
            verdict = agg.finetune_pass_assessment(
                metrics,
                actual_pf=0.8,
                baseline=agg.phase346_cd60_baseline,
            )
            self.assertIn("finetune_pass", verdict)
            self.assertTrue(verdict["checks"]["stop_hit_reduction_at_least_cd60"])

    def test_phase346_baseline_fallback(self) -> None:
        import tempfile

        baseline = _load_phase346_cd60_baseline(Path(tempfile.mkdtemp()))
        self.assertEqual(baseline["label"], "phase346_mfe_lt_0p2_confirm5_cd60")


if __name__ == "__main__":
    unittest.main()

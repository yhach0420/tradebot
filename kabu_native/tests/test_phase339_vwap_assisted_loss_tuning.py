import unittest

from research.phase339_vwap_tuning_evaluation import (
    Phase339IncrementalAggregator,
    _tradeoff_score,
)
from small_paper.vwap_assisted_loss_tuning import (
    VwapAssistedLossTuningPack,
    VwapTuningVariant,
    evaluate_vwap_variant,
    vwap_variant_tick_signal,
)


class TestPhase339VwapAssistedLossTuning(unittest.TestCase):
    def test_vwap_confirm_ticks(self) -> None:
        variant = VwapTuningVariant("t2", below_vwap_confirm_ticks=2)
        ctx = {
            "current_pnl_pct": -0.3,
            "board_imbalance_delta": -0.06,
            "below_vwap": True,
            "vwap_dev_pct": 0.2,
            "vwap_available": True,
        }
        self.assertTrue(vwap_variant_tick_signal(variant=variant, **ctx))
        self.assertFalse(
            evaluate_vwap_variant(variant=variant, below_vwap_streak=1, **ctx)
        )
        self.assertTrue(
            evaluate_vwap_variant(variant=variant, below_vwap_streak=2, **ctx)
        )

    def test_vwap_dev_threshold(self) -> None:
        variant = VwapTuningVariant("dev", min_vwap_dev_pct=0.2)
        self.assertFalse(
            vwap_variant_tick_signal(
                variant=variant,
                current_pnl_pct=-0.3,
                board_imbalance_delta=-0.06,
                below_vwap=True,
                vwap_dev_pct=0.1,
                vwap_available=True,
            )
        )
        self.assertTrue(
            vwap_variant_tick_signal(
                variant=variant,
                current_pnl_pct=-0.3,
                board_imbalance_delta=-0.06,
                below_vwap=True,
                vwap_dev_pct=0.25,
                vwap_available=True,
            )
        )

    def test_vwap_alone_never_triggers(self) -> None:
        variant = VwapTuningVariant("base")
        self.assertFalse(
            vwap_variant_tick_signal(
                variant=variant,
                current_pnl_pct=-0.3,
                board_imbalance_delta=0.02,
                below_vwap=True,
                vwap_dev_pct=0.5,
                vwap_available=True,
            )
        )

    def test_multi_variant_pack_export(self) -> None:
        pack = VwapAssistedLossTuningPack(
            variants=(
                VwapTuningVariant("v1", below_vwap_confirm_ticks=1),
                VwapTuningVariant("v2", below_vwap_confirm_ticks=2),
            )
        )
        self.assertEqual(len(pack.variants), 2)

    def test_tradeoff_score(self) -> None:
        self.assertGreater(_tradeoff_score(-500.0, 2), _tradeoff_score(-2000.0, 1))

    def test_phase339_aggregator_tradeoff(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase339IncrementalAggregator(
                reports_dir=Path(tmp),
                variants=(VwapTuningVariant("v1"),),
            )
            rows = [
                {
                    "position_id": "A",
                    "variant_id": "v1",
                    "symbol": "9984.T",
                    "shadow_pnl_yen_100": 500.0,
                    "actual_pnl_yen_100": -1000.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 1500.0,
                    "no_candidate_trigger": False,
                }
            ]
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260521"},
                trade_rows=rows,
                push_rows=100,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["positions_evaluated"], 1)
            v1 = summary["variants"]["v1"]
            self.assertEqual(v1["stop_hit_reduction_count"], 1)
            self.assertEqual(v1["profit_take_miss_yen_100"], 0.0)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from research.phase341_vwap_robustness_evaluation import (
    VARIANT_ID,
    Phase341RobustnessAggregator,
)
from small_paper.vwap_assisted_loss_tuning import phase341_vwap_dev_0p4pct_variant


class TestPhase341VwapRobustness(unittest.TestCase):
    def _trade_row(self, *, delta: float, sym: str = "9984.T", stop: bool = True) -> dict:
        return {
            "position_id": f"{sym}_1",
            "variant_id": VARIANT_ID,
            "symbol": sym,
            "shadow_pnl_yen_100": -100.0 + delta,
            "actual_pnl_yen_100": -100.0,
            "actual_exit_reason": "stop_hit" if stop else "trailing_mfe",
            "candidate_vs_actual_delta_yen": delta,
            "no_candidate_trigger": delta == 0.0,
        }

    def test_single_variant_only(self) -> None:
        v = phase341_vwap_dev_0p4pct_variant()
        self.assertEqual(v.variant_id, VARIANT_ID)
        self.assertEqual(v.min_vwap_dev_pct, 0.4)

    def test_robustness_pass_when_criteria_met(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase341RobustnessAggregator(reports_dir=Path(tmp))
            for i, sym in enumerate(["A.T", "B.T", "C.T"], start=1):
                agg.ingest_session(
                    session_meta={"session_id": f"s{i}", "day_key": f"d{i}"},
                    trade_rows=[self._trade_row(delta=500.0, sym=sym)],
                    push_rows=1000,
                    runtime_sec=1.0,
                )
            summary = agg.build_summary()
            metrics = summary["variant_metrics"]
            self.assertGreater(metrics["delta_yen"], 0)
            self.assertEqual(metrics["profit_take_miss_yen_100"], 0.0)
            self.assertGreaterEqual(metrics["improved_session_count"], metrics["worsened_session_count"])

    def test_by_symbol_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase341RobustnessAggregator(reports_dir=Path(tmp))
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "d1"},
                trade_rows=[
                    self._trade_row(delta=1000.0, sym="1111.T"),
                    self._trade_row(delta=200.0, sym="2222.T"),
                ],
                push_rows=100,
                runtime_sec=1.0,
            )
            rows = agg.by_symbol_rows()
            self.assertEqual(len(rows), 2)
            paths = agg.finalize_outputs()
            self.assertTrue(Path(paths["by_symbol"]).is_file())


if __name__ == "__main__":
    unittest.main()

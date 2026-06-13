import json
import tempfile
import unittest
from pathlib import Path

from research.phase338_exit_candidate_validation import (
    Phase338IncrementalAggregator,
    _adoption_checks,
    filter_trade_rows_by_candidate,
)
from small_paper.exit_candidate_shadow import PHASE338_CANDIDATE_IDS, ExitCandidateShadowPack


class TestPhase338ExitCandidateValidation(unittest.TestCase):
    def _sample_trade_rows(self) -> list[dict]:
        rows = []
        for cid in PHASE338_CANDIDATE_IDS:
            rows.append(
                {
                    "symbol": "6920.T",
                    "position_id": "6920.T_20260605T100000",
                    "entry_time": "2026-06-05T10:00:00+09:00",
                    "candidate_id": cid,
                    "shadow_pnl_yen_100": 1000.0 if cid == "vwap_assisted_loss_exit" else 500.0,
                    "actual_pnl_yen_100": -500.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 1500.0 if cid == "vwap_assisted_loss_exit" else 1000.0,
                    "no_candidate_trigger": False,
                }
            )
        return rows

    def test_phase338_candidate_filter_on_pack(self) -> None:
        pack = ExitCandidateShadowPack(
            active_candidates=PHASE338_CANDIDATE_IDS,
            enable_extend=False,
        )
        self.assertEqual(pack._candidate_ids(), PHASE338_CANDIDATE_IDS)

    def test_filter_trade_rows_by_candidate(self) -> None:
        rows = self._sample_trade_rows()
        rows.append(
            {
                "candidate_id": "loss_acceleration_exit",
                "position_id": "x",
                "shadow_pnl_yen_100": 0,
                "actual_pnl_yen_100": 0,
                "candidate_vs_actual_delta_yen": 0,
            }
        )
        filtered = filter_trade_rows_by_candidate(rows, PHASE338_CANDIDATE_IDS)
        self.assertEqual(len(filtered), 3)

    def test_incremental_aggregator_session_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase338IncrementalAggregator(
                reports_dir=Path(tmp),
                candidate_ids=PHASE338_CANDIDATE_IDS,
            )
            meta = {"session_id": "push_jsonl/2026-05-21", "day_key": "20260521", "push_dir": "/tmp"}
            agg.ingest_session(
                session_meta=meta,
                trade_rows=self._sample_trade_rows(),
                push_rows=1000,
                runtime_sec=10.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["sessions_evaluated"], 1)
            self.assertEqual(summary["positions_evaluated"], 1)
            self.assertEqual(summary["actual_total_pnl_yen_100"], -500.0)
            vwap = summary["candidates"]["vwap_assisted_loss_exit"]
            self.assertEqual(vwap["delta_yen"], 1500.0)
            self.assertEqual(vwap["improved_session_count"], 1)
            paths = agg.finalize_outputs()
            self.assertTrue(Path(paths["summary"]).is_file())
            self.assertTrue(Path(paths["trades"]).is_file())

    def test_adoption_checks(self) -> None:
        metrics = {
            "delta_yen": 1000.0,
            "profit_factor": 1.5,
            "improved_session_count": 2,
            "worsened_session_count": 1,
            "profit_take_miss_yen_100": -100.0,
            "stop_hit_reduction_count": 1,
            "symbol_concentration": False,
        }
        out = _adoption_checks(metrics, actual_total=-500.0, actual_pf=0.5)
        self.assertTrue(out["adopt_ready"])
        self.assertTrue(out["checks"]["total_pnl_improved"])


if __name__ == "__main__":
    unittest.main()

"""Phase654 20260707 loss attribution tests."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase654_20260707_loss_attribution import (  # noqa: E402
    PHASE654_VERDICT,
    TARGET_DAY,
    _classify_loss_reason,
    _flat_band_block_from_row,
    _rise5_block_from_row,
    run_phase654,
)


class Phase654LossAttributionTests(unittest.TestCase):
    def test_flat_band_block_narrow_cell(self) -> None:
        blocked, reason = _flat_band_block_from_row(
            {"entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0, "entry_type": "PBV2"}
        )
        self.assertTrue(blocked)
        self.assertIn("flat", reason)

    def test_rise5_block_above_threshold(self) -> None:
        blocked, _ = _rise5_block_from_row({"entry_rise_5min_pct": 2.5, "entry_type": "PBV2"})
        self.assertTrue(blocked)

    def test_classify_stop_hit(self) -> None:
        reason = _classify_loss_reason(
            {"pnl_yen_100": -1000, "exit_reason": "stop_hit"},
            latency_alert=False,
        )
        self.assertEqual(reason, "stop_hit")

    def test_classify_no_progress(self) -> None:
        reason = _classify_loss_reason(
            {"pnl_yen_100": -500, "exit_reason": "no_progress_exit"},
            latency_alert=False,
        )
        self.assertEqual(reason, "no_progress")

    def test_run_on_repo_when_data_present(self) -> None:
        day_dir = NATIVE / "results" / "small_paper" / TARGET_DAY
        if not day_dir.is_dir():
            self.skipTest("20260707 session data not present")
        result = run_phase654(repo_root=REPO)
        self.assertEqual(result["verdict"], PHASE654_VERDICT)
        totals = result["totals"]
        self.assertEqual(totals["trade_count"], 91)
        self.assertLess(totals["combined_pnl_yen_100"], 0)
        mandatory = result["mandatory_answers"]
        self.assertIn("1_flat_band_prevented_how_much", mandatory)
        self.assertIn("2_largest_unblocked_loss_pattern", mandatory)

    def test_write_outputs_synthetic(self) -> None:
        from research.phase654_20260707_loss_attribution import Phase654Job

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            kabu = repo / "kabu_native"
            day = kabu / "results" / "small_paper" / TARGET_DAY
            am = day / "live_session_081844"
            pm = day / "live_session_122539"
            for sess in (am, pm):
                sess.mkdir(parents=True)
                self._write_min_session(sess)

            job = Phase654Job(repo_root=repo)
            result = job.run()
            paths = job.write_outputs(result)
            self.assertTrue(paths["report"].is_file())
            report = json.loads(paths["report"].read_text(encoding="utf-8"))
            self.assertEqual(report["verdict"], PHASE654_VERDICT)

    def _write_min_session(self, sess: Path) -> None:
        events = [
            {
                "event_type": "accepted",
                "symbol": "1111.T",
                "entry_time": "2026-07-07T09:10:00+09:00",
                "entry_price": 1000,
                "entry_type": "PBV2",
                "entry_rise_5min_pct": 0.1,
                "entry_rise_10min_pct": 0.0,
                "pbv2_flat_band_shadow_block": True,
                "pbv2_flat_band_shadow_reason": "flat_band_narrow",
            },
            {
                "event_type": "observer_exit",
                "symbol": "1111.T",
                "entry_time": "2026-07-07T09:10:00+09:00",
                "entry_price": 1000,
                "exit_price": 980,
                "pnl_pct": -2.0,
                "exit_reason": "stop_hit",
                "pbv2_flat_band_shadow_block": True,
                "pbv2_flat_band_shadow_delta_yen": 2000.0,
            },
        ]
        with (sess / "small_paper_events.jsonl").open("w", encoding="utf-8") as fh:
            for row in events:
                fh.write(json.dumps(row) + "\n")
        with (sess / "structural_trades.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["symbol", "entry_time", "entry_price", "close_time", "close_price", "close_reason", "mfe_pct", "mae_pct", "hold_duration_sec"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "symbol": "1111.T",
                    "entry_time": "2026-07-07T09:10:00+09:00",
                    "entry_price": 1000,
                    "close_time": "2026-07-07T09:20:00+09:00",
                    "close_price": 980,
                    "close_reason": "stop_hit",
                    "mfe_pct": 0.1,
                    "mae_pct": -2.0,
                    "hold_duration_sec": 600,
                }
            )
        (sess / "small_paper_am_summary.json").write_text(
            json.dumps(
                {
                    "canonical_summary": {"total_pnl_yen_100": -2000},
                    "pbv2_flat_band_shadow_block_count": 1,
                    "pbv2_flat_band_shadow_target_count": 1,
                    "pbv2_flat_band_shadow_delta_yen": 2000.0,
                    "pbv2_flat_band_shadow_actual_total_pnl_yen_100": -2000.0,
                    "pbv2_flat_band_shadow_total_pnl_yen_100": 0.0,
                    "pbv2_rise5_shadow_block_count": 0,
                    "pbv2_rise5_shadow_target_count": 1,
                    "pbv2_rise5_shadow_delta_yen": 0.0,
                }
            ),
            encoding="utf-8",
        )
        (sess / "small_paper_pm_summary.json").write_text(
            json.dumps(
                {
                    "canonical_summary": {"total_pnl_yen_100": -1000},
                    "pbv2_flat_band_shadow_block_count": 0,
                    "pbv2_flat_band_shadow_target_count": 1,
                    "pbv2_flat_band_shadow_delta_yen": 0.0,
                    "pbv2_rise5_shadow_block_count": 0,
                    "pbv2_rise5_shadow_target_count": 1,
                    "pbv2_rise5_shadow_delta_yen": 0.0,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()

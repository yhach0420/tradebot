"""Phase405 time boundary inference tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase405_time_boundary_inference import (  # noqa: E402
    TIME_BUCKETS_MIN,
    _evaluate_mfe_exit_rule,
    _metrics_until,
    build_bucket_snapshot,
    run_phase405_inference,
)


class TestPhase405Inference(unittest.TestCase):
    def test_metrics_until(self) -> None:
        entry_ts = 1_000_000.0
        series = [
            (entry_ts + 60, 1010.0),
            (entry_ts + 300, 1005.0),
            (entry_ts + 600, 990.0),
        ]
        m = _metrics_until(series, entry_ts=entry_ts, entry_price=1000.0, until_ts=entry_ts + 600)
        self.assertGreater(m["max_mfe_so_far_pct"], 0.9)
        self.assertLess(m["current_pnl_pct"], 0)

    def test_snapshot_requires_min_hold(self) -> None:
        trade = {
            "hold_sec": 200.0,
            "entry_ts": 1_000_000.0,
            "entry_price": 1000.0,
            "price_series": [(1_000_100.0, 1001.0)],
            "baseline_pnl_yen_100": 100.0,
            "final_is_winner": True,
            "final_exit_reason": "trailing_mfe",
            "day": "20260601",
            "session": "s",
            "symbol": "3905.T",
            "entry_time": "t",
        }
        self.assertIsNone(build_bucket_snapshot(trade, bucket_min=5))
        trade["hold_sec"] = 400.0
        snap = build_bucket_snapshot(trade, bucket_min=5)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["time_bucket_min"], 5)

    def test_mfe_rule_evaluation(self) -> None:
        trades = [
            {"symbol": "A", "entry_time": "t1", "baseline_pnl_yen_100": -1000.0},
            {"symbol": "B", "entry_time": "t2", "baseline_pnl_yen_100": 500.0},
        ]
        snaps = [
            {
                "symbol": "A",
                "entry_time": "t1",
                "max_mfe_so_far_pct": 0.1,
                "checkpoint_pnl_yen_100": -200.0,
                "final_is_winner": False,
            },
            {
                "symbol": "B",
                "entry_time": "t2",
                "max_mfe_so_far_pct": 0.9,
                "checkpoint_pnl_yen_100": 400.0,
                "final_is_winner": True,
            },
        ]
        row = _evaluate_mfe_exit_rule(trades, snaps, bucket_min=10, threshold=0.5)
        self.assertGreater(row["net_delta_yen"], 0)

    def test_time_bucket_count(self) -> None:
        self.assertEqual(len(TIME_BUCKETS_MIN), 7)

    def test_run_inference(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase405_inference(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["trade_count"], 755)
        self.assertTrue((out / "phase405_time_boundary_inference.csv").is_file())
        self.assertTrue((out / "phase405_time_boundary_policy.csv").is_file())
        ma = result["summary"]["mandatory_answers"]
        self.assertIn("10m", ma)
        self.assertIn("30m", ma)


if __name__ == "__main__":
    unittest.main()

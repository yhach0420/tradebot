"""Phase377: daily regime breakdown tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase377_daily_regime_breakdown import (  # noqa: E402
    PERIOD_A_ID,
    PERIOD_B_ID,
    PRIMARY_STACK,
    STACK_A,
    Phase377DailyRegimeBreakdown,
    _gross_profit_loss_from_day,
    aggregate_period_metrics,
    build_by_day_rows,
    stack_comparison_deltas,
)


class TestPhase377DailyRegimeBreakdown(unittest.TestCase):
    def test_gross_profit_loss_from_day(self) -> None:
        gp, gl = _gross_profit_loss_from_day(-244600.0, 0.1249)
        self.assertIsNotNone(gp)
        self.assertIsNotNone(gl)
        self.assertAlmostEqual(gp - gl, -244600.0, places=0)

    def test_aggregate_period_metrics(self) -> None:
        daily = [
            {
                "day": "20260518",
                "stack_id": PRIMARY_STACK,
                "trade_count": "10",
                "win_count": "4",
                "loss_count": "6",
                "total_pnl_yen_100": "-1000",
                "profit_factor": "0.5",
                "stop_hit_count": "2",
                "low_mfe_stop_hit_count": "1",
                "trailing_mfe_exit_count": "3",
                "dynamic40_pnl_yen_100": "-800",
                "core10_pnl_yen_100": "-200",
                "am_pnl_yen_100": "-1000",
                "pm_pnl_yen_100": "",
            }
        ]
        equity = [
            {
                "day": "20260518",
                "stack_id": PRIMARY_STACK,
                "daily_pnl_yen_100": "-1000",
                "cumulative_pnl_yen_100": "-1000",
                "drawdown_yen_100": "-1000",
                "running_peak_yen_100": "0",
            }
        ]
        m = aggregate_period_metrics(daily, equity, period_id=PERIOD_A_ID, stack_id=PRIMARY_STACK)
        self.assertEqual(m["trade_count"], 10)
        self.assertEqual(m["total_pnl_yen_100"], -1000.0)
        self.assertEqual(m["stop_hit_count"], 2)

    def test_stack_comparison_deltas(self) -> None:
        rows = [
            {
                "period_id": PERIOD_A_ID,
                "stack_id": STACK_A,
                "total_pnl_yen_100": -100.0,
                "profit_factor": 0.8,
                "stop_hit_count": 5,
                "trade_count": 10,
            },
            {
                "period_id": PERIOD_A_ID,
                "stack_id": PRIMARY_STACK,
                "total_pnl_yen_100": 50.0,
                "profit_factor": 1.2,
                "stop_hit_count": 3,
                "trade_count": 8,
            },
        ]
        deltas = stack_comparison_deltas(rows)
        c_vs_a = next(d for d in deltas if d["comparison"] == "C_vs_A")
        self.assertEqual(c_vs_a["pnl_delta"], 150.0)
        self.assertEqual(c_vs_a["stop_hit_delta"], -2)

    def test_run_on_phase376_outputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase376_production_daily_pnl.csv").is_file():
            self.skipTest("phase376 outputs missing")
        audit = Phase377DailyRegimeBreakdown(reports_dir=reports)
        result = audit.run(max_workers=2)
        cons = result["consistency_checks"]
        self.assertTrue(cons.get("total_pnl_matches"))
        self.assertTrue(cons.get("trade_count_matches"))
        c_a = result["period_metrics"][PERIOD_A_ID][PRIMARY_STACK]
        c_b = result["period_metrics"][PERIOD_B_ID][PRIMARY_STACK]
        self.assertEqual(c_a["total_pnl_yen_100"] + c_b["total_pnl_yen_100"], 40790.0)
        self.assertTrue(loss := result["loss_concentration"])
        self.assertTrue(loss.get("q2_period_b_total_pnl_positive"))
        self.assertTrue(loss.get("q3_period_b_pf_above_1"))


if __name__ == "__main__":
    unittest.main()

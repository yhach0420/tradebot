"""Phase389 full-regime validation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase377_daily_regime_breakdown import PERIOD_A_ID, PERIOD_B_ID  # noqa: E402
from research.phase389_full_regime_live_candidate_validation import (  # noqa: E402
    build_period_a_loss_analysis,
    build_required_answers,
    load_phase377_reference,
    period_id_for_day,
    regime_metrics,
)


class TestPhase389FullRegime(unittest.TestCase):
    def test_period_id(self) -> None:
        self.assertEqual(period_id_for_day("20260520"), PERIOD_A_ID)
        self.assertEqual(period_id_for_day("20260601"), PERIOD_B_ID)

    def test_regime_metrics(self) -> None:
        accepted = [
            {"period_id": PERIOD_A_ID, "pnl_yen": -1000.0, "exit_reason": "stop_hit", "universe_group": "dynamic40", "peak_mfe_pct": 0.1},
            {"period_id": PERIOD_A_ID, "pnl_yen": 500.0, "exit_reason": "trailing_mfe_exit", "universe_group": "core10", "peak_mfe_pct": 0.8},
        ]
        m = regime_metrics(accepted, period_id=PERIOD_A_ID, daily_pnls={"20260520": -500.0}, equity_curve=[])
        self.assertEqual(m["trade_count"], 2)
        self.assertEqual(m["total_pnl_yen"], -500.0)

    def test_period_a_loss_analysis(self) -> None:
        accepted = [
            {
                "period_id": PERIOD_A_ID,
                "pnl_yen": -5000.0,
                "exit_reason": "stop_hit",
                "peak_mfe_pct": 0.1,
                "entry_score": 2.0,
                "symbol": "1111.T",
                "entry_time": "t1",
                "exit_time": "t2",
            }
        ]
        rows, summary = build_period_a_loss_analysis(accepted, reject_breakdown={"max_concurrent_positions": 10})
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary["period_a_entry_trade_count"], 1)
        self.assertIn("diagnosis", summary)

    def test_required_answers_profitable(self) -> None:
        full = {"total_pnl_yen": 100000, "final_equity": 1600000, "max_drawdown_yen": 50000, "min_maintenance_ratio": 0.55, "force_exit_count": 0, "equity_floor_breached": False, "maintenance_stop_count": 0}
        a = {"total_pnl_yen": -30000}
        b = {"total_pnl_yen": 130000}
        ans = build_required_answers(full, a, b)
        self.assertTrue(ans["full_period_profitable"])
        self.assertTrue(ans["recommend_1500k"])

    def test_phase377_reference_loader(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        ref = load_phase377_reference(reports)
        self.assertTrue(ref.get("loaded"))
        stack = ref.get("stack_c_by_period") or {}
        self.assertIn(PERIOD_A_ID, stack)
        self.assertIn(PERIOD_B_ID, stack)


if __name__ == "__main__":
    unittest.main()

"""Phase406 portfolio adoption tests."""

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

from research.phase406_portfolio_adoption import (  # noqa: E402
    _classify_tier,
    aggregate_portfolio_metrics,
    load_phase405_boundary_policy,
    run_phase406_portfolio_adoption,
)


class TestPhase406Portfolio(unittest.TestCase):
    def test_tier_s(self) -> None:
        baseline = {
            "total_pnl_yen_100": 100.0,
            "profit_factor": 1.1,
            "max_drawdown_yen_100": 1000.0,
            "final_equity_yen": 1_500_100.0,
        }
        better = {
            "total_pnl_yen_100": 200.0,
            "profit_factor": 1.2,
            "max_drawdown_yen_100": 800.0,
            "final_equity_yen": 1_500_200.0,
        }
        self.assertEqual(_classify_tier(better, baseline=baseline), "Tier S")

    def test_tier_reject(self) -> None:
        baseline = {
            "total_pnl_yen_100": 100.0,
            "profit_factor": 1.1,
            "max_drawdown_yen_100": 1000.0,
            "final_equity_yen": 1_500_100.0,
        }
        worse = {
            "total_pnl_yen_100": 50.0,
            "profit_factor": 1.0,
            "max_drawdown_yen_100": 1200.0,
            "final_equity_yen": 1_500_050.0,
        }
        self.assertEqual(_classify_tier(worse, baseline=baseline), "Reject")

    def test_load_phase405_policy(self) -> None:
        path = REPO / "results" / "reports" / "phase405_time_boundary_policy.csv"
        if not path.is_file():
            self.skipTest("phase405 policy missing")
        rules = load_phase405_boundary_policy(path)
        self.assertIn(30, rules)
        self.assertEqual(rules[30].mfe_exit, 0.6)

    def test_aggregate_metrics(self) -> None:
        trades = [
            {"shadow_pnl_yen_100": 100.0, "shadow_exit_reason": "trailing_mfe", "hold_sec": 60, "shadow_exit_time": "2026-06-01T10:00:00+09:00"},
            {"shadow_pnl_yen_100": -50.0, "shadow_exit_reason": "stop_hit", "hold_sec": 120, "shadow_exit_time": "2026-06-01T11:00:00+09:00"},
        ]
        m = aggregate_portfolio_metrics(
            trades,
            policy_label="test",
            baseline_pnls=[0.0, 0.0],
            p90_hold=9999.0,
        )
        self.assertEqual(m["trade_count"], 2)
        self.assertEqual(m["total_pnl_yen_100"], 50.0)

    def test_run_portfolio_adoption(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        out = REPO / "results" / "reports"
        result = run_phase406_portfolio_adoption(repo_root=REPO, trades_path=src, output_dir=out)
        self.assertEqual(result["summary"]["trade_count"], 755)
        ranks = result["summary"]["mandatory_ranks"]
        self.assertIsNotNone(ranks.get("rank_1"))
        self.assertTrue((out / "phase406_portfolio_adoption_comparison.csv").is_file())


if __name__ == "__main__":
    unittest.main()

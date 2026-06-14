"""Phase269 portfolio configuration optimization tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase269_portfolio_configuration_optimization import (  # noqa: E402
    CAP_LEVELS,
    LEVERAGES,
    STARTING_EQUITIES,
    STOP_POLICIES,
    build_required_answers,
    config_id,
    rank_configurations,
    simulate_configuration,
)


class TestPhase269PortfolioConfigurationOptimization(unittest.TestCase):
    def test_config_id(self) -> None:
        cid = config_id(starting_equity=1_500_000, leverage=2.0, cap=2, stop_policy="fixed_stop_1p2")
        self.assertIn("eq1500k", cid)
        self.assertIn("lev2p0", cid)
        self.assertIn("cap2", cid)

    def test_simulate_small_grid(self) -> None:
        trades = [
            {
                "symbol": "1001.T",
                "entry_time": "2026-05-29T09:10:00+09:00",
                "exit_time": "2026-05-29T09:30:00+09:00",
                "entry_price": 1000.0,
                "pnl_yen_100": 1000.0,
                "realized_pnl_pct": 1.0,
                "mae_pct": -0.2,
            }
        ]
        result = simulate_configuration(
            trades,
            starting_equity=1_500_000,
            leverage=2.0,
            cap=2,
            stop_policy="fixed_stop_1p2",
        )
        self.assertIn("dual_layer", result)
        self.assertIn("research_layer", result["dual_layer"])
        self.assertIn("live_simulation_layer", result["dual_layer"])
        self.assertEqual(result["dual_layer"]["adoption_verdict"]["primary_metric"], "final_equity")
        self.assertGreater(result["final_equity"], 1_500_000)

    def test_grid_size_constants(self) -> None:
        self.assertEqual(
            len(STARTING_EQUITIES) * len(LEVERAGES) * len(CAP_LEVELS) * len(STOP_POLICIES),
            150,
        )

    def test_run_on_repo(self) -> None:
        from research.equity_curve_shadow import load_period_trades
        from research.phase269_portfolio_configuration_optimization import (
            run_portfolio_configuration_optimization,
        )

        trades, _ = load_period_trades(REPO)
        if not trades:
            self.skipTest("no period trades")
        result = run_portfolio_configuration_optimization(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
        )
        self.assertEqual(result["phase"], "269-Portfolio-Configuration-Optimization")
        self.assertEqual(result["grid_stats"]["configuration_count"], 150)
        answers = build_required_answers(
            [simulate_configuration(trades, starting_equity=eq, leverage=lev, cap=cap, stop_policy=stop)
             for eq, lev, cap, stop in [(1_500_000, 2.0, 2, "fixed_stop_1p2")]]
        )
        self.assertIn("1_max_final_equity_configuration", answers)
        self.assertIn("6_recommended_live_configuration", answers)


if __name__ == "__main__":
    unittest.main()

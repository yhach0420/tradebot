"""Phase267 equity curve shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.equity_curve_shadow import (  # noqa: E402
    SCENARIO_ACTUAL,
    SCENARIO_DYNAMIC,
    STARTING_EQUITY,
    compute_scenario_metrics,
    load_period_trades,
    pnl_for_actual_fixed_stop,
    pnl_for_dynamic_stop_risk_1p0,
    run_equity_curve_shadow,
    simulate_equity_curve_scenario,
)


class TestEquityCurveShadow(unittest.TestCase):
    def test_pnl_resolvers(self) -> None:
        trade = {
            "entry_price": 1000.0,
            "pnl_yen_100": 100.0,
            "realized_pnl_pct": 0.1,
            "mae_pct": -0.5,
        }
        actual = pnl_for_actual_fixed_stop(trade, shares=100, entry_equity=STARTING_EQUITY)
        dynamic = pnl_for_dynamic_stop_risk_1p0(trade, shares=100, entry_equity=STARTING_EQUITY)
        self.assertEqual(actual, 100.0)
        self.assertIsInstance(dynamic, float)

    def test_simulate_small_sample(self) -> None:
        trades = [
            {
                "symbol": "1001.T",
                "day": "20260529",
                "entry_time": "2026-05-29T09:10:00+09:00",
                "exit_time": "2026-05-29T09:30:00+09:00",
                "close_time": "2026-05-29T09:30:00+09:00",
                "entry_price": 1000.0,
                "exit_price": 1010.0,
                "pnl_yen_100": 1000.0,
                "realized_pnl_pct": 1.0,
                "mae_pct": -0.2,
            }
        ]
        actual = simulate_equity_curve_scenario(
            trades,
            scenario_id=SCENARIO_ACTUAL,
            pnl_resolver=pnl_for_actual_fixed_stop,
        )
        self.assertGreater(actual["final_equity"], STARTING_EQUITY)

    def test_run_on_repo(self) -> None:
        trades, meta = load_period_trades(REPO)
        if not trades:
            self.skipTest("no period trades")
        self.assertGreater(meta["input_trade_count"], 0)
        result = run_equity_curve_shadow(repo_root=REPO, reports_dir=REPO / "kabu_native" / "results" / "reports")
        self.assertEqual(result["phase"], "267-Equity-Curve-Shadow")
        self.assertIn(SCENARIO_ACTUAL, result.get("scenarios") or {})
        self.assertIn(SCENARIO_DYNAMIC, result.get("scenarios") or {})
        dual = result.get("dual_layer") or {}
        self.assertIn(SCENARIO_ACTUAL, dual)
        self.assertEqual(
            dual[SCENARIO_ACTUAL]["adoption_verdict"]["primary_metric"],
            "final_equity",
        )


if __name__ == "__main__":
    unittest.main()

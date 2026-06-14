"""Phase271 leverage attribution and robustness tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase271_leverage_attribution_and_robustness import (  # noqa: E402
    decompose_leverage_attribution,
    practical_significance,
    simulate_audited,
)


class TestPhase271LeverageAttribution(unittest.TestCase):
    def test_practical_significance(self) -> None:
        row = practical_significance(equity_yen=2_500_000, final_lev15=2_550_000, final_lev20=2_500_000)
        self.assertFalse(row["economically_insignificant"])

    def test_simulate_audited_small(self) -> None:
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
        sim = simulate_audited(
            trades,
            starting_equity=2_500_000,
            leverage=1.5,
            cap=5,
            stop_policy="dynamic_stop_risk_1p0",
        )
        self.assertIn("accepted_pnls", sim)
        self.assertGreater(sim["final_equity"], 2_500_000)

    def test_decompose(self) -> None:
        base = {"final_equity": 100.0, "accepted_pnls": {"a": 10.0}, "accepted_trade_count": 1, "max_drawdown_pct": 5.0, "reject_log": []}
        ch = {"final_equity": 115.0, "accepted_pnls": {"a": 10.0, "b": 5.0}, "accepted_trade_count": 2, "max_drawdown_pct": 4.0, "reject_log": []}
        row = decompose_leverage_attribution(base, ch, equity_yen=1000)
        self.assertEqual(row["delta_final_equity_yen"], 15.0)

    def test_run_on_repo(self) -> None:
        from research.equity_curve_shadow import load_period_trades
        from research.phase271_leverage_attribution_and_robustness import (
            run_leverage_attribution_and_robustness,
        )

        trades, _ = load_period_trades(REPO)
        if not trades:
            self.skipTest("no trades")
        result = run_leverage_attribution_and_robustness(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
            bootstrap_iterations=200,
        )
        self.assertEqual(result["phase"], "271-Leverage-Attribution-and-Robustness")
        self.assertIn("required_answers", result)


if __name__ == "__main__":
    unittest.main()

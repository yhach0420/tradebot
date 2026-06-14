"""Phase384 capital scaling study tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase384_capital_scaling_study import (  # noqa: E402
    CAPITAL_LEVELS,
    SCENARIO_SPECS,
    _scenario_worker,
    build_recommendations,
    build_scaling_analysis,
    simulate_unconstrained,
)


class TestPhase384CapitalScaling(unittest.TestCase):
    def _trade(
        self,
        *,
        ep: float = 1000.0,
        xp: float = 1010.0,
        symbol: str = "1234.T",
        entry: str = "2026-06-01T09:05:00+09:00",
        exit_t: str = "2026-06-01T09:30:00+09:00",
    ) -> dict:
        return {
            "symbol": symbol,
            "entry_time": entry,
            "exit_time": exit_t,
            "entry_price": ep,
            "exit_price": xp,
            "pnl_pct": round((xp - ep) / ep * 100.0, 4),
            "pnl_yen_100": round((xp - ep) * 100.0, 2),
            "exit_reason_canonical": "trailing_mfe_exit",
            "session_kind": "am",
            "universe_group": "dynamic40",
            "day_key": "20260601",
        }

    def test_unconstrained_accepts_all(self) -> None:
        trades = [
            self._trade(),
            self._trade(symbol="5678.T", entry="2026-06-01T09:10:00+09:00", exit_t="2026-06-01T09:40:00+09:00"),
        ]
        result = simulate_unconstrained(trades, initial_equity=500_000)
        self.assertEqual(result["accepted_trade_count"], 2)
        self.assertEqual(result["rejected_trade_count"], 0)
        self.assertEqual(result["total_pnl_yen"], 2000.0)

    def test_scenario_worker_matrix(self) -> None:
        trades = [self._trade(ep=100.0, xp=110.0)]
        job = {
            "scenario_letter": "A",
            "trades": trades,
            "initial_equity": 500_000.0,
            "equity_floor": 250_000.0,
        }
        row = _scenario_worker(job)
        self.assertEqual(row["scenario_letter"], "A")
        self.assertEqual(row["accepted_trade_count"], 1)

    def test_capital_levels_count(self) -> None:
        self.assertEqual(len(CAPITAL_LEVELS), 6)
        self.assertEqual(len(SCENARIO_SPECS), 6)

    def test_scaling_analysis_thresholds(self) -> None:
        rows = [
            {
                "initial_equity": 500_000,
                "scenario_letter": "B",
                "accepted_rate": 0.10,
                "accepted_trade_count": 10,
                "rejected_trade_count": 90,
                "total_pnl_yen": 1000,
                "profit_factor": 1.1,
                "min_maintenance_above_0p5": True,
                "force_exit_count": 0,
            },
            {
                "initial_equity": 1_000_000,
                "scenario_letter": "B",
                "accepted_rate": 0.55,
                "accepted_trade_count": 55,
                "rejected_trade_count": 45,
                "total_pnl_yen": 8000,
                "profit_factor": 1.4,
                "min_maintenance_above_0p5": True,
                "force_exit_count": 0,
            },
            {
                "initial_equity": 500_000,
                "scenario_letter": "F",
                "accepted_rate": 1.0,
                "accepted_trade_count": 100,
                "rejected_trade_count": 0,
                "total_pnl_yen": 10_000,
                "profit_factor": 2.0,
            },
        ]
        analysis = build_scaling_analysis(rows, unconstrained_pnl=10_000)
        self.assertEqual(analysis["accepted_rate_threshold_capital"]["25pct"]["B"], 1_000_000)
        self.assertEqual(analysis["pnl_recovery_threshold_capital"]["50pct"]["B"], 1_000_000)

    def test_recommendations_structure(self) -> None:
        rows = [
            {
                "initial_equity": 500_000,
                "scenario_letter": "C",
                "accepted_rate": 0.10,
                "total_pnl_yen": -1000,
                "min_maintenance_above_0p5": False,
                "force_exit_count": 0,
                "equity_floor_breached": False,
            },
            {
                "initial_equity": 1_000_000,
                "scenario_letter": "B",
                "accepted_rate": 0.60,
                "total_pnl_yen": 8000,
                "min_maintenance_above_0p5": True,
                "force_exit_count": 0,
                "equity_floor_breached": False,
            },
            {
                "initial_equity": 1_000_000,
                "scenario_letter": "C",
                "accepted_rate": 0.55,
                "total_pnl_yen": 7000,
                "min_maintenance_above_0p5": True,
                "force_exit_count": 0,
                "equity_floor_breached": False,
            },
            {
                "initial_equity": 500_000,
                "scenario_letter": "F",
                "accepted_rate": 1.0,
                "total_pnl_yen": 10_000,
            },
        ]
        analysis = build_scaling_analysis(rows, unconstrained_pnl=10_000)
        rec = build_recommendations(rows, analysis)
        self.assertIn("recommended_minimum_capital", rec)
        self.assertIn("recommended_operating_capital", rec)
        self.assertTrue(rec["is_500k_insufficient"])


if __name__ == "__main__":
    unittest.main()

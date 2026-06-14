"""Phase388 1.5M live candidate validation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase388_cap1500k_live_candidate_validation import (  # noqa: E402
    CANDIDATE_EQUITY,
    build_required_answers,
    simulate_detailed,
)


class TestPhase388Cap1500k(unittest.TestCase):
    def _trade(
        self,
        *,
        symbol: str = "1234.T",
        entry: str = "2026-06-01T09:05:00+09:00",
        exit_t: str = "2026-06-01T09:30:00+09:00",
        ep: float = 100.0,
        xp: float = 110.0,
    ) -> dict:
        return {
            "symbol": symbol,
            "entry_time": entry,
            "exit_time": exit_t,
            "entry_price": ep,
            "exit_price": xp,
            "pnl_yen_100": round((xp - ep) * 100.0, 2),
            "pnl_pct": round((xp - ep) / ep * 100.0, 4),
            "exit_reason_canonical": "trailing_mfe_exit",
            "peak_mfe_pct": 0.8,
            "day_key": "20260601",
        }

    def test_simulate_detailed(self) -> None:
        trades = [self._trade(), self._trade(symbol="5678.T", entry="2026-06-01T10:05:00+09:00", exit_t="2026-06-01T10:30:00+09:00")]
        result = simulate_detailed(trades, scenario_id="test", cap=2, initial_equity=CANDIDATE_EQUITY)
        self.assertEqual(result["accepted_trade_count"], 2)
        self.assertGreater(result["total_pnl_yen"], 0)
        self.assertIn("reject_reason_breakdown", result)

    def test_required_answers_profitable(self) -> None:
        cand = {"total_pnl_yen": 80000, "min_maintenance_ratio": 0.55, "force_exit_count": 0, "equity_floor_breached": False, "maintenance_stop_count": 0, "maintenance_warning_count": 0}
        ref = {"total_pnl_yen": 162700}
        ans = build_required_answers(cand, ref)
        self.assertTrue(ans["is_1500k_profitable"])
        self.assertEqual(ans["pnl_delta_vs_2m_cap2_yen"], -82700.0)


if __name__ == "__main__":
    unittest.main()

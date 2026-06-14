"""Phase383 realistic credit sizing tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase382_capital_constrained_backtest import LOT_SIZE
from research.phase383_realistic_credit_sizing_backtest import (  # noqa: E402
    compute_buying_power,
    compute_requested_shares,
    simulate_scenario,
    SCENARIO_SPECS,
)


class TestPhase383RealisticCreditSizing(unittest.TestCase):
    def _trade(self, *, ep: float = 1000.0, xp: float = 1010.0, symbol: str = "1234.T", entry: str = "2026-06-01T09:05:00+09:00", exit_t: str = "2026-06-01T09:30:00+09:00") -> dict:
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

    def test_buying_power_model(self) -> None:
        self.assertEqual(compute_buying_power(equity=500_000, gross=200_000, leverage_limit=2.0), 800_000)
        self.assertEqual(compute_buying_power(equity=500_000, gross=0, leverage_limit=3.0), 1_500_000)

    def test_fixed_100_only_cap(self) -> None:
        spec = SCENARIO_SPECS["A_cash_100_only"]
        shares, reason = compute_requested_shares(
            spec=spec, equity=500_000, entry_price=1000.0, buying_power=500_000
        )
        self.assertEqual(shares, 100)
        self.assertIsNone(reason)

    def test_fixed_100_only_never_multi_lot(self) -> None:
        trades = [self._trade(ep=100.0), self._trade(symbol="5678.T", ep=200.0, xp=210.0, entry="2026-06-01T09:10:00+09:00", exit_t="2026-06-01T09:40:00+09:00")]
        result = simulate_scenario(trades, scenario_id="A_cash_100_only", spec=SCENARIO_SPECS["A_cash_100_only"])
        for row in result["_trade_log"]:
            if row.get("accepted_or_rejected") == "accepted" and row.get("shares"):
                self.assertLessEqual(int(row["shares"]), 100)

    def test_credit2_vs_credit3_buying_power(self) -> None:
        trades = [self._trade(ep=5000.0, xp=5050.0)]
        r2 = simulate_scenario(trades, scenario_id="B_credit2_100_only", spec=SCENARIO_SPECS["B_credit2_100_only"])
        r3 = simulate_scenario(trades, scenario_id="C_credit3_100_only", spec=SCENARIO_SPECS["C_credit3_100_only"])
        self.assertEqual(r2["accepted_trade_count"], 1)
        self.assertEqual(r3["accepted_trade_count"], 1)

    def test_reinvestment(self) -> None:
        trades = [
            self._trade(symbol="1111.T", entry="2026-06-01T09:05:00+09:00", exit_t="2026-06-01T09:30:00+09:00", ep=100.0, xp=150.0),
            self._trade(symbol="2222.T", entry="2026-06-01T10:05:00+09:00", exit_t="2026-06-01T10:30:00+09:00", ep=100.0, xp=150.0),
        ]
        result = simulate_scenario(trades, scenario_id="B_credit2_100_only", spec=SCENARIO_SPECS["B_credit2_100_only"])
        self.assertTrue(result["reinvestment_effective"])
        self.assertGreater(result["last_entry_equity"], result["first_entry_equity"])

    def test_risk_capped_at_100(self) -> None:
        spec = SCENARIO_SPECS["H_credit2_risk_0p5pct_100max"]
        shares, _ = compute_requested_shares(spec=spec, equity=1_000_000, entry_price=100.0, buying_power=2_000_000)
        self.assertEqual(shares, 100)


if __name__ == "__main__":
    unittest.main()

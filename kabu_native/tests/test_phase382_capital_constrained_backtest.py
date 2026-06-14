"""Phase382 capital-constrained backtest tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase382_capital_constrained_backtest import (  # noqa: E402
    EQUITY_FLOOR,
    INITIAL_EQUITY,
    compute_requested_shares,
    simulate_scenario,
    validate_trade_row,
)


class TestPhase382CapitalConstrained(unittest.TestCase):
    def _trade(
        self,
        *,
        symbol: str = "1234.T",
        entry: str = "2026-06-01T09:05:00+09:00",
        exit_t: str = "2026-06-01T09:30:00+09:00",
        ep: float = 1000.0,
        xp: float = 1010.0,
        session_kind: str = "am",
        universe_group: str = "dynamic40",
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
            "session_kind": session_kind,
            "universe_group": universe_group,
            "day_key": "20260601",
        }

    def test_validate_trade_row(self) -> None:
        ok, _ = validate_trade_row(self._trade())
        self.assertTrue(ok)
        bad, reason = validate_trade_row({"symbol": "X"})
        self.assertFalse(bad)
        self.assertEqual(reason, "missing_entry_time")

    def test_compute_requested_shares_fixed(self) -> None:
        spec = {"sizing": "fixed_100", "leverage_limit": 3.0}
        shares, reason = compute_requested_shares(
            scenario_id="A_fixed_100_shares",
            spec=spec,
            equity=INITIAL_EQUITY,
            entry_price=1000.0,
            gross=0.0,
            buying_power=INITIAL_EQUITY * 3.0,
        )
        self.assertEqual(shares, 100)
        self.assertIsNone(reason)

    def test_reference_unconstrained_matches_pnl(self) -> None:
        trades = [self._trade(), self._trade(symbol="5678.T", ep=2000.0, xp=1990.0)]
        result = simulate_scenario(trades, scenario_id="X", unconstrained=True)
        expected = sum(t["pnl_yen_100"] for t in trades)
        self.assertEqual(result["accepted_trade_count"], 2)
        self.assertEqual(result["realized_pnl"], expected)

    def test_insufficient_buying_power_rejects(self) -> None:
        expensive = self._trade(symbol="9999.T", ep=50000.0, xp=50500.0)
        result = simulate_scenario([expensive], scenario_id="A_fixed_100_shares")
        self.assertEqual(result["accepted_trade_count"], 0)
        self.assertEqual(result["rejected_trade_count"], 1)

    def test_concurrent_position_limit(self) -> None:
        trades = [
            self._trade(symbol="1111.T", entry="2026-06-01T09:05:00+09:00", exit_t="2026-06-01T12:00:00+09:00"),
            self._trade(symbol="2222.T", entry="2026-06-01T09:06:00+09:00", exit_t="2026-06-01T12:00:00+09:00"),
            self._trade(symbol="3333.T", entry="2026-06-01T09:07:00+09:00", exit_t="2026-06-01T12:00:00+09:00"),
            self._trade(symbol="4444.T", entry="2026-06-01T09:08:00+09:00", exit_t="2026-06-01T12:00:00+09:00"),
        ]
        result = simulate_scenario(trades, scenario_id="A_fixed_100_shares")
        self.assertEqual(result["accepted_trade_count"], 3)
        self.assertEqual(result["rejected_trade_count"], 1)

    def test_equity_floor_breach_halts(self) -> None:
        losers = [
            self._trade(
                symbol=f"{i}.T",
                entry=f"2026-06-01T09:{i:02d}:00+09:00",
                exit_t=f"2026-06-01T10:{i:02d}:00+09:00",
                ep=1000.0,
                xp=100.0,
            )
            for i in range(30)
        ]
        result = simulate_scenario(losers, scenario_id="A_fixed_100_shares")
        self.assertTrue(
            result["equity_floor_breached"]
            or result["trading_halted"]
            or float(result["min_equity"]) < INITIAL_EQUITY * 0.6
        )


if __name__ == "__main__":
    unittest.main()

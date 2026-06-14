"""Phase385 cap sensitivity tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase385_cap_sensitivity_study import (  # noqa: E402
    BASELINE_CAP,
    CAP_LEVELS,
    build_cap3_comparison,
    build_recommendation,
    pick_best_caps,
    simulate_cap,
)


class TestPhase385CapSensitivity(unittest.TestCase):
    def _trade(
        self,
        *,
        ep: float = 1000.0,
        xp: float = 1010.0,
        symbol: str = "1234.T",
        entry: str = "2026-06-01T09:05:00+09:00",
        exit_t: str = "2026-06-01T09:30:00+09:00",
        reason: str = "trailing_mfe_exit",
        peak_mfe: float = 0.8,
    ) -> dict:
        return {
            "symbol": symbol,
            "entry_time": entry,
            "exit_time": exit_t,
            "entry_price": ep,
            "exit_price": xp,
            "pnl_pct": round((xp - ep) / ep * 100.0, 4),
            "pnl_yen_100": round((xp - ep) * 100.0, 2),
            "exit_reason_canonical": reason,
            "peak_mfe_pct": peak_mfe,
            "session_kind": "am",
            "universe_group": "dynamic40",
            "day_key": "20260601",
        }

    def test_cap1_rejects_more_than_cap3(self) -> None:
        trades = [
            self._trade(symbol=f"{i}.T", entry=f"2026-06-01T09:0{i}:00+09:00", exit_t=f"2026-06-01T10:0{i}:00+09:00")
            for i in range(1, 5)
        ]
        r1 = simulate_cap(trades, cap=1)
        r3 = simulate_cap(trades, cap=3)
        self.assertLess(r1["accepted_trade_count"], r3["accepted_trade_count"])
        self.assertGreater(r1["position_cap_reject_count"], 0)

    def test_cap_increases_accepted(self) -> None:
        trades = [
            self._trade(symbol="1111.T", entry="2026-06-01T09:05:00+09:00", exit_t="2026-06-01T09:20:00+09:00"),
            self._trade(symbol="2222.T", entry="2026-06-01T09:06:00+09:00", exit_t="2026-06-01T09:25:00+09:00"),
            self._trade(symbol="3333.T", entry="2026-06-01T09:07:00+09:00", exit_t="2026-06-01T09:35:00+09:00"),
            self._trade(symbol="4444.T", entry="2026-06-01T09:08:00+09:00", exit_t="2026-06-01T09:40:00+09:00"),
        ]
        accepted = [simulate_cap(trades, cap=c)["accepted_trade_count"] for c in CAP_LEVELS]
        self.assertEqual(accepted, sorted(accepted))

    def test_exit_reason_counts(self) -> None:
        trades = [
            self._trade(reason="trailing_mfe_exit"),
            self._trade(symbol="2222.T", reason="overlap_replaced", entry="2026-06-01T10:05:00+09:00", exit_t="2026-06-01T10:30:00+09:00"),
            self._trade(symbol="3333.T", reason="stop_hit", peak_mfe=0.1, xp=990.0, entry="2026-06-01T11:05:00+09:00", exit_t="2026-06-01T11:30:00+09:00"),
        ]
        result = simulate_cap(trades, cap=6)
        self.assertEqual(result["accepted_trade_count"], 3)
        self.assertEqual(result["trailing_mfe_exit_count"], 1)
        self.assertEqual(result["overlap_replaced_count"], 1)
        self.assertEqual(result["low_mfe_stop_count"], 1)

    def test_cap3_comparison(self) -> None:
        rows = [
            {"cap": 3, "accepted_trade_count": 10, "total_pnl_yen_100": 1000, "stop_hit_count": 2, "low_mfe_stop_count": 1, "trailing_mfe_exit_count": 4, "overlap_replaced_count": 1},
            {"cap": 4, "accepted_trade_count": 12, "total_pnl_yen_100": 1500, "stop_hit_count": 3, "low_mfe_stop_count": 2, "trailing_mfe_exit_count": 5, "overlap_replaced_count": 2},
        ]
        comp = build_cap3_comparison(rows)
        self.assertEqual(comp["4"]["delta_accepted_vs_cap3"], 2)
        self.assertEqual(comp["4"]["delta_pnl_yen_vs_cap3"], 500)

    def test_pick_best_caps(self) -> None:
        rows = [
            {"cap": 3, "total_pnl_yen_100": 1000, "profit_factor": 1.2, "risk_adjusted_return": 0.5, "max_drawdown_yen": 100, "force_exit_count": 0, "equity_floor_breached": False},
            {"cap": 4, "total_pnl_yen_100": 1200, "profit_factor": 1.5, "risk_adjusted_return": 0.8, "max_drawdown_yen": 120, "force_exit_count": 0, "equity_floor_breached": False},
        ]
        best = pick_best_caps(rows)
        self.assertEqual(best["best_pnl_cap"], 4)
        self.assertEqual(best["best_pf_cap"], 4)

    def test_baseline_cap_is_three(self) -> None:
        self.assertEqual(BASELINE_CAP, 3)


if __name__ == "__main__":
    unittest.main()

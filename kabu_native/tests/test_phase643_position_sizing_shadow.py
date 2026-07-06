"""Phase643: position sizing shadow research tests (research only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase643_position_sizing_shadow import (  # noqa: E402
    LiquidityTertiles,
    MIN_LOT,
    _pbv2_target_shares,
    _session_kind,
    _tier_shares,
    build_liquidity_tertiles,
    compute_variant_shares,
    simulate_variant,
)


class Phase643SizingTests(unittest.TestCase):
    def test_fixed_100_baseline(self) -> None:
        shares, reason = compute_variant_shares(
            "fixed_100",
            equity=1_000_000,
            entry_price=1000,
            trade={},
            liquidity=LiquidityTertiles(),
        )
        self.assertEqual(shares, 100)
        self.assertIsNone(reason)

    def test_equity_pct_rounds_to_100_lot(self) -> None:
        shares, _ = compute_variant_shares(
            "equity_30pct",
            equity=1_000_000,
            entry_price=2500,
            trade={},
            liquidity=LiquidityTertiles(),
        )
        self.assertEqual(shares, 100)
        self.assertGreaterEqual(shares % MIN_LOT, 0)

    def test_equity_insufficient_skips(self) -> None:
        shares, reason = compute_variant_shares(
            "equity_50pct",
            equity=50_000,
            entry_price=5000,
            trade={},
            liquidity=LiquidityTertiles(),
        )
        self.assertEqual(shares, 0)
        self.assertEqual(reason, "below_min_lot")

    def test_risk_sizing_from_stop(self) -> None:
        shares, reason = compute_variant_shares(
            "risk_1.00pct",
            equity=3_000_000,
            entry_price=1000,
            trade={},
            liquidity=LiquidityTertiles(),
        )
        self.assertGreaterEqual(shares, MIN_LOT)
        self.assertIsNone(reason)

    def test_pbv2_score_linked_configurable(self) -> None:
        self.assertEqual(_pbv2_target_shares(3), 100)
        self.assertEqual(_pbv2_target_shares(4), 200)
        self.assertEqual(_pbv2_target_shares(5), 300)

    def test_liquidity_tiers(self) -> None:
        liq = LiquidityTertiles(tv_lo=50e6, tv_hi=200e6)
        self.assertEqual(_tier_shares(10e6, liq.tv_lo, liq.tv_hi), 100)
        self.assertEqual(_tier_shares(100e6, liq.tv_lo, liq.tv_hi), 200)
        self.assertEqual(_tier_shares(300e6, liq.tv_lo, liq.tv_hi), 300)

    def test_simulate_scales_pnl(self) -> None:
        trades = [
            {
                "day": "2026-06-25",
                "symbol": "1111.T",
                "entry_time": "2026-06-25T10:00:00+09:00",
                "entry_price": 1000,
                "pnl_yen_100": 1000,
                "entry_pool": "PBV2",
                "session_kind": "AM",
                "price_tier": "mid_price",
                "price_band": "1000-3000",
            }
        ]
        liq = LiquidityTertiles()
        fixed = simulate_variant(trades, variant_key="fixed_100", initial_equity=1_000_000, liquidity=liq)
        eq30 = simulate_variant(trades, variant_key="equity_30pct", initial_equity=1_000_000, liquidity=liq)
        self.assertEqual(fixed["total_pnl_yen"], 1000)
        self.assertGreaterEqual(eq30["total_pnl_yen"], fixed["total_pnl_yen"])

    def test_session_kind_am_pm(self) -> None:
        self.assertEqual(_session_kind("2026-06-25T10:00:00+09:00"), "AM")
        self.assertEqual(_session_kind("2026-06-25T13:00:00+09:00"), "PM")

    def test_tertiles_from_trades(self) -> None:
        trades = [
            {"trading_value": 10e6, "turnover_proxy": 0.1, "update_count_before_entry": 1},
            {"trading_value": 100e6, "turnover_proxy": 1.0, "update_count_before_entry": 10},
            {"trading_value": 500e6, "turnover_proxy": 5.0, "update_count_before_entry": 30},
        ]
        liq = build_liquidity_tertiles(trades)
        self.assertLess(liq.tv_lo, liq.tv_hi)


if __name__ == "__main__":
    unittest.main()

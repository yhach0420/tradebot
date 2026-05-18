"""paper_trade Tier1/Tier2 dynamic watchlist helpers (unittest)."""
from __future__ import annotations

import unittest

import market.yahoo.watch as ykw


class PaperTierWatchlistTests(unittest.TestCase):
    def test_merge_dynamic_watchlist_nested(self) -> None:
        file_cfg = {"paper_trade": {"dynamic_watchlist": {"enabled": True, "max_symbols": 20}}}
        d = ykw._paper_trade_merge_runtime_controls(file_cfg, None)
        dw = d.get("dynamic_watchlist")
        self.assertIsInstance(dw, dict)
        self.assertTrue(bool(dw.get("enabled")))
        self.assertEqual(int(dw.get("max_symbols")), 20)
        self.assertEqual(float(dw.get("refresh_sec")), 300.0)

    def test_merge_tier2_sticky_then_ranked(self) -> None:
        ranked = [
            ("7203", 0.9, {"momentum_score": 0.9}),
            ("6758", 0.5, {"momentum_score": 0.5}),
            ("9984", 0.4, {"momentum_score": 0.4}),
        ]
        prev = ["9984", "7203"]
        sticky = {"9984"}
        out, changed = ykw._paper_merge_tier2_watchlist(
            prev,
            ranked,
            universe={"7203", "6758", "9984"},
            max_symbols=2,
            sticky=sticky,
        )
        self.assertEqual(out, ["9984", "7203"])
        self.assertFalse(changed)

        out2, ch2 = ykw._paper_merge_tier2_watchlist(
            ["6758"],
            ranked,
            universe={"7203", "6758", "9984"},
            max_symbols=2,
            sticky=set(),
        )
        self.assertEqual(out2, ["7203", "6758"])
        self.assertTrue(ch2)

    def test_max_pct_move_est(self) -> None:
        m = ykw._paper_trade_max_pct_move_est(100.0, 98.0, 104.0, 100.5, 98.0, 104.0)
        self.assertAlmostEqual(m, 0.5, places=6)

    def test_chart_aux_scores_from_extras(self) -> None:
        pt = {
            "highs_1m": [100.0, 100.5, 101.0, 100.8, 102.0, 101.5, 102.5, 102.0, 103.0],
            "vols_1m": [1000.0, 1100.0, 1200.0, 900.0, 800.0, 700.0],
            "closes_1m": [99.0, 100.0, 101.0, 100.5, 102.0],
            "lows_1m": [98.5, 99.0, 99.5, 99.8, 100.0],
        }
        class _I:
            vwap_distance_pct = 1.0

        out = ykw._paper_trade_chart_aux_scores_from_extras(pt, _I())
        self.assertIn("high_refresh_intraday_score", out)
        self.assertGreaterEqual(float(out["high_refresh_intraday_score"]), 0.0)
        self.assertLessEqual(float(out["high_refresh_intraday_score"]), 1.0)

    def test_tier1_score_breakdown_range_and_aux(self) -> None:
        q = ykw.Quote(
            symbol="7203.T",
            price=104.0,
            currency="JPY",
            previous_close=100.0,
            change_percent=4.0,
            day_high=105.0,
            day_low=99.0,
            volume=1_000_000.0,
            market_time_utc=None,
            market_cap=None,
        )
        br = ykw._paper_tier1_score_breakdown(
            q,
            vol_ema_for_spike=500_000.0,
            median_change=2.0,
            mad_change=1.0,
            chart_aux={
                "high_refresh_intraday_score": 0.8,
                "volume_acceleration_intraday_score": 0.7,
                "vwap_closeness_intraday_score": 0.6,
                "intraday_range_pressure_score": 0.9,
            },
        )
        self.assertAlmostEqual(float(br["range_breakout_pressure_score"]), (104.0 - 99.0) / (105.0 - 99.0), places=5)
        self.assertAlmostEqual(float(br["high_refresh_intraday_score"]), 0.8, places=5)


if __name__ == "__main__":
    unittest.main()

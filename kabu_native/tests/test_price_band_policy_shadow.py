"""Phase259 price band policy shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.price_band_policy_shadow import (  # noqa: E402
    BASELINE_POLICY,
    POLICIES,
    build_policy_candidates,
    candidate_passes_policy,
    price_band_label,
    run_price_band_policy_shadow,
    select_dynamic_for_policy,
)


class TestPriceBandPolicyShadow(unittest.TestCase):
    def test_price_band_label(self) -> None:
        self.assertEqual(price_band_label(250), "<300")
        self.assertEqual(price_band_label(3500), "3000+")

    def test_candidate_passes_policy(self) -> None:
        low_row = {"symbol": "1001.T", "close": "250", "volatility_liquidity_score": "10"}
        high_row = {"symbol": "1002.T", "close": "5000", "volatility_liquidity_score": "10"}
        self.assertFalse(candidate_passes_policy(low_row, BASELINE_POLICY))
        self.assertFalse(candidate_passes_policy(low_row, "allow_high_keep_low_filter"))
        self.assertTrue(candidate_passes_policy(low_row, "allow_low_keep_high_filter"))
        self.assertTrue(candidate_passes_policy(high_row, "allow_high_keep_low_filter"))

    def test_soft_cap_limits_high_price(self) -> None:
        candidates = [
            {"symbol": "3001.T", "close": 4000.0, "volatility_liquidity_score": 100.0, "score": 100.0},
            {"symbol": "3002.T", "close": 4500.0, "volatility_liquidity_score": 99.0, "score": 99.0},
            {"symbol": "3003.T", "close": 5000.0, "volatility_liquidity_score": 98.0, "score": 98.0},
            {"symbol": "3004.T", "close": 5500.0, "volatility_liquidity_score": 97.0, "score": 97.0},
            {"symbol": "1001.T", "close": 500.0, "volatility_liquidity_score": 50.0, "score": 50.0},
        ]
        dynamic, _ = select_dynamic_for_policy(candidates, policy="high_price_soft_cap_3", dynamic_slots=4)
        high = {s for s in dynamic if s.startswith("300")}
        self.assertLessEqual(len(high), 3)

    def test_build_policy_candidates_risk_adjusted(self) -> None:
        features = [
            {"symbol": "1001.T", "close": "500", "volatility_liquidity_score": "100"},
            {"symbol": "1002.T", "close": "6000", "volatility_liquidity_score": "100"},
        ]
        close_map = {"1001.T": 500.0, "1002.T": 6000.0}
        rows = build_policy_candidates(
            features,
            core_symbols=set(),
            policy="high_price_risk_adjusted_score",
            close_map=close_map,
        )
        by_sym = {r["symbol"]: r["score"] for r in rows}
        self.assertLess(by_sym["1002.T"], by_sym["1001.T"])

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        result = run_price_band_policy_shadow(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "259-PriceBand-Policy-Shadow")
        self.assertEqual(len(result.get("policies") or []), len(POLICIES))
        self.assertGreater(len(result.get("_trade_rows") or []), 0)


if __name__ == "__main__":
    unittest.main()

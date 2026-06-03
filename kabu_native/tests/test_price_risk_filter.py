"""Unit tests for universe price_risk_filter (Phase250)."""

import unittest

from universe.price_risk_filter import (
    MIN_CLOSE_PRICE,
    dynamic_price_risk_fail_reason,
    passes_dynamic_price_risk,
)


class TestPriceRiskFilter(unittest.TestCase):
    def test_min_close_price_is_300(self) -> None:
        self.assertEqual(MIN_CLOSE_PRICE, 300.0)

    def test_rejects_below_300(self) -> None:
        row = {"close": "250.0", "volatility_liquidity_score": "100"}
        self.assertFalse(passes_dynamic_price_risk(row))
        reason = dynamic_price_risk_fail_reason(close_price=250.0, tick_ratio=0.1)
        self.assertIn("close_below_300", reason)

    def test_passes_at_or_above_300(self) -> None:
        row = {"close": "300.0", "volatility_liquidity_score": "100"}
        self.assertTrue(passes_dynamic_price_risk(row))


if __name__ == "__main__":
    unittest.main()

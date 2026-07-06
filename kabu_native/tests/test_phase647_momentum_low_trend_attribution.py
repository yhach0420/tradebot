"""Phase647: momentum low trend attribution tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase647_momentum_low_trend_attribution import (  # noqa: E402
    TREND_DOWN,
    TREND_STRONG_DOWN,
    TREND_UP,
    classify_trend,
    counterfactual_exclude,
    enrich_momentum_low_trades,
    is_pbv2_momentum_low,
)


class Phase647TrendTests(unittest.TestCase):
    def test_is_pbv2_momentum_low(self) -> None:
        self.assertTrue(
            is_pbv2_momentum_low(
                {"entry_pool": "PBV2", "momentum_continuation": 0.2}
            )
        )
        self.assertFalse(is_pbv2_momentum_low({"entry_pool": "OR", "momentum_continuation": 0.2}))
        self.assertFalse(is_pbv2_momentum_low({"entry_pool": "PBV2", "momentum_continuation": 0.3}))

    def test_classify_strong_down(self) -> None:
        label, _ = classify_trend(
            {"entry_rise_5min_pct": -0.6, "entry_rise_10min_pct": -1.0, "entry_vwap_dev_pct": -1.0}
        )
        self.assertEqual(label, TREND_STRONG_DOWN)

    def test_classify_up(self) -> None:
        label, _ = classify_trend(
            {"entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.15, "entry_vwap_dev_pct": 0.5}
        )
        self.assertEqual(label, TREND_UP)

    def test_counterfactual_excludes_down(self) -> None:
        trades = [
            {"trend_label": TREND_DOWN, "pnl_yen_100": -100.0, "entry_time": "t1", "symbol": "A"},
            {"trend_label": TREND_UP, "pnl_yen_100": 200.0, "entry_time": "t2", "symbol": "B"},
        ]
        cf = counterfactual_exclude(trades, exclude_labels={TREND_DOWN})
        self.assertEqual(cf["kept_entry_count"], 1)
        self.assertGreater(cf["delta_pnl_yen_100"], 0)

    def test_enrich_filters_or(self) -> None:
        rows = enrich_momentum_low_trades(
            [
                {"entry_pool": "OR", "momentum_continuation": 0.1, "pnl_yen_100": 1},
                {"entry_pool": "PBV2", "momentum_continuation": 0.1, "pnl_yen_100": 1, "entry_rise_5min_pct": 0.1, "entry_rise_10min_pct": 0.1},
            ]
        )
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()

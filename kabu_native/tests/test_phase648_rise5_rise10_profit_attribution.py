"""Phase648: rise5 × rise10 profit attribution tests."""

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

from research.phase648_rise5_rise10_profit_attribution import (  # noqa: E402
    counterfactual_block,
    enrich_pbv2_rise,
    filter_pbv2_trades,
    heatmap_cell,
    is_pbv2_entry,
    rise_band_label,
    _block_rise5_lt,
)


class Phase648RiseTests(unittest.TestCase):
    def test_is_pbv2_entry(self) -> None:
        self.assertTrue(is_pbv2_entry({"entry_pool": "PBV2"}))
        self.assertFalse(is_pbv2_entry({"entry_pool": "OR"}))

    def test_rise_band_label(self) -> None:
        self.assertEqual(rise_band_label(-2.5), "-3~-2%")
        self.assertEqual(rise_band_label(0.3), "0~0.5%")
        self.assertEqual(rise_band_label(4.0), ">3%")

    def test_heatmap_cell(self) -> None:
        self.assertEqual(heatmap_cell(-1.0, 0.1), "Rise5 Down × Rise10 Flat")
        self.assertEqual(heatmap_cell(1.0, 2.0), "Rise5 Up × Rise10 Up")

    def test_filter_pbv2(self) -> None:
        rows = filter_pbv2_trades(
            [{"entry_pool": "PBV2", "pnl_yen_100": 1}, {"entry_pool": "OR", "pnl_yen_100": 2}]
        )
        self.assertEqual(len(rows), 1)

    def test_counterfactual_blocks_negative_rise5(self) -> None:
        trades = [
            {"entry_rise_5min_pct": -1.5, "pnl_yen_100": -100.0, "entry_time": "t1", "symbol": "A"},
            {"entry_rise_5min_pct": 0.5, "pnl_yen_100": 200.0, "entry_time": "t2", "symbol": "B"},
        ]
        enriched = enrich_pbv2_rise(trades)
        cf = counterfactual_block(enriched, condition_id="rise5_lt_-1", block_fn=_block_rise5_lt(-1.0))
        self.assertEqual(cf["kept_entry_count"], 1)
        self.assertGreater(cf["delta_pnl_yen_100"], 0)

    def test_enrich_bands(self) -> None:
        rows = enrich_pbv2_rise([{"entry_rise_5min_pct": -0.8, "entry_rise_10min_pct": 0.2}])
        self.assertEqual(rows[0]["rise5_band"], "-1~-0.5%")
        self.assertEqual(rows[0]["rise5_heatmap"], "Down")


if __name__ == "__main__":
    unittest.main()

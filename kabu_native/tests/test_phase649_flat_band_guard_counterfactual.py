"""Phase649 flat-band guard counterfactual tests."""

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

from research.phase649_flat_band_guard_counterfactual import (  # noqa: E402
    apply_variant,
    block_flat_band_narrow,
    block_flat_cell_only,
    block_flat_plus_overheat,
    block_weak_motion_guard,
    filter_pbv2_trades,
)


class Phase649FlatBandTests(unittest.TestCase):
    def test_filter_pbv2(self) -> None:
        rows = filter_pbv2_trades(
            [{"entry_pool": "PBV2", "pnl_yen_100": 1}, {"entry_pool": "OR", "pnl_yen_100": 2}]
        )
        self.assertEqual(len(rows), 1)

    def test_flat_cell_only(self) -> None:
        self.assertTrue(
            block_flat_cell_only({"entry_rise_5min_pct": 0.1, "entry_rise_10min_pct": -0.2})
        )
        self.assertFalse(
            block_flat_cell_only({"entry_rise_5min_pct": 1.0, "entry_rise_10min_pct": 0.1})
        )

    def test_flat_band_narrow(self) -> None:
        self.assertTrue(
            block_flat_band_narrow({"entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.1})
        )
        self.assertFalse(
            block_flat_band_narrow({"entry_rise_5min_pct": -0.2, "entry_rise_10min_pct": 0.1})
        )

    def test_weak_motion_guard(self) -> None:
        self.assertTrue(
            block_weak_motion_guard({"entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": -0.3})
        )

    def test_flat_plus_overheat(self) -> None:
        self.assertTrue(block_flat_plus_overheat({"entry_rise_5min_pct": 2.5}))
        self.assertTrue(
            block_flat_plus_overheat({"entry_rise_5min_pct": 0.2, "entry_rise_10min_pct": 0.0})
        )

    def test_apply_variant_delta(self) -> None:
        trades = [
            {"entry_rise_5min_pct": 0.1, "entry_rise_10min_pct": 0.0, "pnl_yen_100": -100.0, "entry_time": "t1", "symbol": "A"},
            {"entry_rise_5min_pct": 1.0, "entry_rise_10min_pct": 1.0, "pnl_yen_100": 200.0, "entry_time": "t2", "symbol": "B"},
        ]
        baseline = {"entry_count": 2, "pnl_yen_100": 100.0, "max_dd_yen_100": -100.0, "profit_factor": 1.0}
        v = apply_variant(
            trades,
            variant_id="flat_band_narrow",
            label="test",
            block_fn=block_flat_band_narrow,
            baseline_metrics=baseline,
        )
        self.assertEqual(v["blocked_entry_count"], 1)
        self.assertGreater(v["delta_pnl_yen_100"], 0)


if __name__ == "__main__":
    unittest.main()

"""Phase656 winner attribution tests."""

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

from research.phase656_winner_attribution import (  # noqa: E402
    PHASE656_VERDICT,
    _classify_pnl_bucket,
    _counterfactual_variants,
    _importance_rows,
    _percentile,
    run_phase656,
)


class Phase656WinnerAttributionTests(unittest.TestCase):
    def test_pnl_buckets(self) -> None:
        self.assertEqual(_classify_pnl_bucket(100, p10=-50, p30=0, p70=20, p90=80), "big_winner")
        self.assertEqual(_classify_pnl_bucket(-100, p10=-50, p30=0, p70=20, p90=80), "big_loser")

    def test_percentile(self) -> None:
        self.assertEqual(_percentile([1, 2, 3, 4], 50), 2.5)

    def test_importance_rows(self) -> None:
        trades = []
        for i in range(10):
            trades.append(
                {
                    "pnl_bucket": "big_winner",
                    "pnl_yen_100": 5000 + i * 100,
                    "continuation_quality": 0.8 + i * 0.01,
                    "momentum_continuation": 0.5,
                }
            )
        for i in range(10):
            trades.append(
                {
                    "pnl_bucket": "big_loser",
                    "pnl_yen_100": -5000 - i * 100,
                    "continuation_quality": 0.2,
                    "momentum_continuation": 0.05,
                }
            )
        rows = _importance_rows(trades, pool="test")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["feature_id"], "continuation_quality")

    def test_counterfactual_baseline(self) -> None:
        trades = [
            {
                "entry_pool": "PBV2",
                "pnl_yen_100": 1000,
                "pnl_bucket": "big_winner",
                "continuation_quality": 0.9,
                "momentum_continuation": 0.5,
                "board_imbalance": 0.6,
                "trading_value": 2e8,
                "price_age_sec": 1.0,
                "entry_rise_5min_pct": 1.0,
                "day": "2026-07-01",
                "symbol": "1111.T",
                "entry_time": "t1",
            },
            {
                "entry_pool": "PBV2",
                "pnl_yen_100": -1000,
                "pnl_bucket": "big_loser",
                "continuation_quality": 0.1,
                "momentum_continuation": 0.0,
                "board_imbalance": 0.1,
                "trading_value": 1e7,
                "price_age_sec": 8.0,
                "entry_rise_5min_pct": -1.0,
                "day": "2026-07-01",
                "symbol": "2222.T",
                "entry_time": "t2",
            },
        ]
        bw = {"continuation_quality_p25": 0.5, "momentum_continuation_p25": 0.2, "board_imbalance_p25": 0.3, "trading_value_p50": 1e8, "price_age_sec_p75": 5.0, "price_age_sec_p50": 3.0, "board_imbalance_p50": 0.4, "momentum_continuation_p50": 0.3}
        bl = {"continuation_quality_p75": 0.3, "momentum_continuation_p75": 0.1, "entry_vwap_dev_pct_p25": -2.0}
        rows = _counterfactual_variants(trades, pool="PBV2", bw_profile=bw, bl_profile=bl)
        self.assertEqual(rows[0]["variant_id"], "baseline")
        self.assertGreaterEqual(len(rows), 3)

    def test_run_on_repo_when_data_present(self) -> None:
        if not (NATIVE / "results" / "small_paper").is_dir():
            self.skipTest("Phase634 dataset not present")
        result = run_phase656(repo_root=REPO)
        self.assertEqual(result["verdict"], PHASE656_VERDICT)
        mandatory = result["mandatory_answers"]
        self.assertIn("9_final_verdict", mandatory)
        self.assertIn(mandatory["9_final_verdict"], ("ADOPT", "HOLD", "REJECT"))


if __name__ == "__main__":
    unittest.main()

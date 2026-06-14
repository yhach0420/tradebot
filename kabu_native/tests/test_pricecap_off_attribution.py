"""Phase258 price cap OFF attribution tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.pricecap_off_attribution import (  # noqa: E402
    CAP_OFF_PATTERN,
    build_price_band_attribution_rows,
    build_verdict,
    price_band_label,
    run_pricecap_off_attribution,
)


class TestPriceCapOffAttribution(unittest.TestCase):
    def test_price_band_label(self) -> None:
        self.assertEqual(price_band_label(250), "<300")
        self.assertEqual(price_band_label(450), "300-500")
        self.assertEqual(price_band_label(3500), "3000+")

    def test_build_price_band_attribution_rows(self) -> None:
        close_map = {"1001.T": 250.0, "1002.T": 4000.0}
        baseline = {"1001.T"}
        rows = build_price_band_attribution_rows(
            day="20260522",
            pattern=CAP_OFF_PATTERN,
            dynamic_symbols={"1001.T", "1002.T"},
            baseline_dynamic=baseline,
            trades=[],
            close_map=close_map,
        )
        self.assertEqual(len(rows), 5)
        low_row = next(r for r in rows if r["price_band"] == "<300")
        self.assertEqual(low_row["added_symbol_count"], 0)

    def test_build_verdict_adopt_blocked(self) -> None:
        verdict = build_verdict(
            trade_overlap_days=["20260520"],
            total_delta=1000.0,
            low_price_row={"total_pnl_yen_100": 800.0, "band_delta_pnl_yen_100": 800.0, "contribution_to_total_delta": 0.8},
            high_price_row={"total_pnl_yen_100": 0.0, "worst_trade_pnl_yen_100": -100.0},
            cap_off_band_rows=[],
            trade_validation=[],
        )
        self.assertTrue(verdict["adopt_not_allowed"])

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase257_universe_diff_by_pattern.csv").is_file():
            self.skipTest("phase257 outputs missing")
        result = run_pricecap_off_attribution(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "258-PriceCap-Off-Attribution")
        self.assertGreater(len(result.get("_price_band_rows") or []), 0)


if __name__ == "__main__":
    unittest.main()

"""Phase257 Core12 Dynamic38 PriceCap shadow review tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.core12_dynamic38_pricecap_shadow_review import (  # noqa: E402
    BASELINE_PATTERN,
    PATTERN_SPECS,
    PATTERNS,
    _price_band,
    build_pattern_universe,
    discover_validation_days,
    run_core12_dynamic38_pricecap_shadow_review,
    simulate_core_symbols,
)


class TestCore12Dynamic38PriceCapShadowReview(unittest.TestCase):
    def test_pattern_specs(self) -> None:
        self.assertEqual(PATTERN_SPECS[BASELINE_PATTERN].core_slots, 10)
        self.assertEqual(PATTERN_SPECS["shadow_core12_dynamic38_pricecap_on"].dynamic_slots, 38)

    def test_price_band(self) -> None:
        self.assertEqual(_price_band(250), "<300")
        self.assertEqual(_price_band(5000), "3000-10000")

    def test_simulate_core_symbols(self) -> None:
        features = [
            {"symbol": "1001.T", "volatility_liquidity_score": "100", "close": "500"},
            {"symbol": "1002.T", "volatility_liquidity_score": "90", "close": "500"},
            {"symbol": "1003.T", "volatility_liquidity_score": "80", "close": "500"},
        ]
        core = simulate_core_symbols({"1000.T"}, features, core_slots=3, price_cap_on=True)
        self.assertEqual(len(core), 3)

    def test_build_pattern_universe_actual(self) -> None:
        actual_core = {"1000.T"}
        actual_dynamic = {f"200{i}.T" for i in range(40)}
        core, dynamic, _ = build_pattern_universe(
            pattern=BASELINE_PATTERN,
            actual_core=actual_core,
            actual_dynamic=actual_dynamic,
            actual_rank_map={},
            feature_rows=[],
            sector_map={},
            top3_map={},
        )
        self.assertEqual(core, actual_core)
        self.assertEqual(dynamic, actual_dynamic)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        result = run_core12_dynamic38_pricecap_shadow_review(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "257-Core12-Dynamic38-PriceCap-Shadow-Review")
        self.assertEqual(len(result.get("_diff_rows") or []), len(PATTERNS) * (result["summary"]["simulated_day_count"] or 0))

    def test_discover_validation_days(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        top3 = reports / "phase246_sector_heat_tomorrow_top3.csv"
        if not top3.is_file():
            self.skipTest("phase246 top3 missing")
        days = discover_validation_days(reports, top3)
        self.assertGreater(len(days), 0)


if __name__ == "__main__":
    unittest.main()

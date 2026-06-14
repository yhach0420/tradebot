"""Phase249-SectorHeat-Universe-Shadow-Simulation tests."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.market_sector_heat_universe_shadow import (  # noqa: E402
    PATTERNS,
    MarketSectorHeatUniverseShadowSimulation,
    build_day_shadow_results,
    pattern_adjustment,
    sector_heat_rank_label,
    select_shadow_dynamic40,
)


class TestMarketSectorHeatUniverseShadow(unittest.TestCase):
    def test_sector_heat_rank_label(self) -> None:
        top3 = {"電気機器": 1, "情報・通信業": 2}
        self.assertEqual(sector_heat_rank_label("電気機器", top3), "rank1")
        self.assertEqual(sector_heat_rank_label("銀行業", top3), "none")

    def test_pattern_adjustment(self) -> None:
        self.assertEqual(pattern_adjustment("actual", 1), 1.0)
        self.assertGreater(pattern_adjustment("sector_bonus_rank1_only", 1), 1.0)
        self.assertEqual(pattern_adjustment("sector_bonus_rank1_only", 2), 1.0)
        self.assertLess(
            pattern_adjustment("sector_bonus_top3_with_rank1_overheat_penalty", 1),
            pattern_adjustment("sector_bonus_top3", 1),
        )

    def test_shadow_day_with_synthetic_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp)
            top3_path = reports / "phase246_sector_heat_tomorrow_top3.csv"
            with top3_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "signal_day",
                        "validation_day",
                        "rank",
                        "sector_33_name",
                        "heat_score",
                        "daily_return_pct",
                        "trading_value_increase_pct",
                        "pm_return_pct_1400_1530",
                        "continuation_days",
                    ]
                )
                w.writerow(["20260519", "20260520", 1, "電気機器", 3.0, 1.0, 1.0, 1.0, 1])
                w.writerow(["20260519", "20260520", 2, "情報・通信業", 2.5, 1.0, 1.0, 1.0, 1])
                w.writerow(["20260519", "20260520", 3, "非鉄金属", 2.0, 1.0, 1.0, 1.0, 1])

            features_path = reports / "features_20260519.csv"
            with features_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "close", "volatility_liquidity_score"])
                w.writerow(["1001.T", "500", "100"])
                w.writerow(["1002.T", "500", "90"])
                w.writerow(["1003.T", "500", "80"])
                w.writerow(["1004.T", "500", "70"])

            universe_path = reports / "universe_core10_dynamic40_price_risk_am_20260520.csv"
            with universe_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "universe_slot", "rank", "volatility_liquidity_score"])
                w.writerow(["9001.T", "core", "1", ""])
                w.writerow(["1004.T", "dynamic", "11", "70"])

            sector_map = {
                "1001.T": "電気機器",
                "1002.T": "情報・通信業",
                "1003.T": "非鉄金属",
                "1004.T": "銀行業",
                "9001.T": "銀行業",
            }
            trades = [
                {"symbol": "1001.T", "pnl_yen_100": 100.0},
                {"symbol": "1004.T", "pnl_yen_100": -50.0},
            ]
            result = build_day_shadow_results(
                validation_day="20260520",
                signal_day="20260519",
                top3_map={"電気機器": 1, "情報・通信業": 2, "非鉄金属": 3},
                reports_dir=reports,
                sector_map=sector_map,
                trades_for_day=trades,
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(len(result["diff_rows"]), len(PATTERNS))
            bonus_row = next(
                r for r in result["diff_rows"] if r["pattern"] == "sector_bonus_top3"
            )
            self.assertIn("1001.T", bonus_row["added_symbols"].split("|"))

    def test_select_shadow_dynamic40_changes_membership(self) -> None:
        candidates = [
            {"symbol": "1001.T", "volatility_liquidity_score": 100.0, "sector_heat_rank_num": 1},
            {"symbol": "1002.T", "volatility_liquidity_score": 90.0, "sector_heat_rank_num": None},
        ]
        actual = {"1002.T"}
        ranks = {"1002.T": 1}
        syms, _ = select_shadow_dynamic40(
            candidates,
            pattern="sector_bonus_rank1_only",
            actual_dynamic=actual,
            actual_rank_map=ranks,
        )
        self.assertIn("1001.T", syms)

    def test_run_on_repo_inputs(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase246_sector_heat_tomorrow_top3.csv").is_file():
            self.skipTest("phase246 top3 missing")
        sim = MarketSectorHeatUniverseShadowSimulation(repo_root=REPO, reports_dir=reports)
        result = sim.run()
        paths = sim.write_outputs(result)
        self.assertTrue(paths["summary"].is_file())
        self.assertTrue(result["constraints"]["yaml_changes_forbidden"])


if __name__ == "__main__":
    unittest.main()

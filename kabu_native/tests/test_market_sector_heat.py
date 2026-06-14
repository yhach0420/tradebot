"""Phase246-SectorHeat-Observation tests."""

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

from research.market_sector_heat import (  # noqa: E402
    MarketSectorHeatObservation,
    TOP_SECTOR_COUNT,
    build_tomorrow_top3_rows,
    build_validation_rows,
    compute_continuation_days,
    compute_heat_scores,
    compute_trading_value_increase,
    load_symbol_day_metrics,
    rank_normalize,
    summarize_validation,
)


def _write_intraday_csv(path: Path, bars: list[tuple[str, float, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "open", "high", "low", "close", "volume"])
        for ts, o, c, vol in bars:
            w.writerow([ts, o, c, c, c, vol])


class TestMarketSectorHeat(unittest.TestCase):
    def test_rank_normalize(self) -> None:
        ranks = rank_normalize({"a": 1.0, "b": 2.0, "c": 3.0})
        self.assertEqual(ranks["a"], 0.0)
        self.assertEqual(ranks["c"], 1.0)

    def test_load_symbol_day_metrics_pm_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "7203.T.csv"
            bars = [
                ("2026-05-15T00:00:00+00:00", 100.0, 101.0, 1000.0),
                ("2026-05-15T05:00:00+00:00", 110.0, 111.0, 500.0),
                ("2026-05-15T06:00:00+00:00", 111.0, 115.0, 600.0),
            ]
            _write_intraday_csv(path, bars)
            m = load_symbol_day_metrics(path, sector="輸送用機器")
            self.assertIsNotNone(m)
            assert m is not None
            self.assertAlmostEqual(m.daily_return_pct, 15.0, places=2)
            self.assertAlmostEqual(m.pm_return_pct_1400_1530, 4.5455, places=2)
            self.assertGreater(m.trading_value_jpy, 0.0)

    def test_heat_score_and_top3(self) -> None:
        sector_rows_by_day = {
            "20260514": {
                "A": {
                    "sector_33_name": "A",
                    "daily_return_pct": 1.0,
                    "trading_value_jpy": 100.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": 0.5,
                    "continuation_days": 0,
                },
                "B": {
                    "sector_33_name": "B",
                    "daily_return_pct": -1.0,
                    "trading_value_jpy": 50.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": -0.5,
                    "continuation_days": 0,
                },
                "C": {
                    "sector_33_name": "C",
                    "daily_return_pct": 0.2,
                    "trading_value_jpy": 70.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": 0.1,
                    "continuation_days": 0,
                },
            },
            "20260515": {
                "A": {
                    "sector_33_name": "A",
                    "daily_return_pct": 2.0,
                    "trading_value_jpy": 200.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": 1.0,
                    "continuation_days": 0,
                },
                "B": {
                    "sector_33_name": "B",
                    "daily_return_pct": 0.5,
                    "trading_value_jpy": 80.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": 0.2,
                    "continuation_days": 0,
                },
                "C": {
                    "sector_33_name": "C",
                    "daily_return_pct": 0.1,
                    "trading_value_jpy": 60.0,
                    "trading_value_increase_pct": None,
                    "pm_return_pct_1400_1530": 0.0,
                    "continuation_days": 0,
                },
            },
        }
        compute_trading_value_increase(sector_rows_by_day)
        compute_continuation_days(sector_rows_by_day)
        for day in sector_rows_by_day:
            compute_heat_scores(sector_rows_by_day[day])

        top3 = build_tomorrow_top3_rows(sector_rows_by_day, available_days=["20260514", "20260515"])
        self.assertEqual(len(top3), TOP_SECTOR_COUNT)
        self.assertEqual(top3[0]["signal_day"], "20260514")
        self.assertEqual(top3[0]["validation_day"], "20260515")
        self.assertEqual(top3[0]["sector_33_name"], "A")

    def test_validation_metrics(self) -> None:
        tomorrow_top3 = [
            {
                "signal_day": "20260519",
                "validation_day": "20260520",
                "rank": 1,
                "sector_33_name": "電気機器",
            },
            {
                "signal_day": "20260519",
                "validation_day": "20260520",
                "rank": 2,
                "sector_33_name": "情報・通信業",
            },
            {
                "signal_day": "20260519",
                "validation_day": "20260520",
                "rank": 3,
                "sector_33_name": "その他製品",
            },
        ]
        trades_by_day = {
            "20260520": [
                {"symbol": "6758.T", "pnl_yen_100": 100.0},
                {"symbol": "9984.T", "pnl_yen_100": -50.0},
                {"symbol": "9999.T", "pnl_yen_100": 20.0},
            ]
        }
        sector_map = {
            "6758.T": "電気機器",
            "9984.T": "情報・通信業",
            "9999.T": "unknown",
        }
        sector_rows_by_day = {
            "20260520": {
                "電気機器": {"daily_return_pct": 1.2},
                "情報・通信業": {"daily_return_pct": -0.3},
                "その他製品": {"daily_return_pct": 0.4},
            }
        }
        rows = build_validation_rows(
            tomorrow_top3,
            trades_by_day=trades_by_day,
            sector_map=sector_map,
            sector_rows_by_day=sector_rows_by_day,
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["predicted_sector_trade_count"], 2)
        self.assertEqual(row["predicted_sector_pnl_yen_100"], 50.0)
        self.assertEqual(row["predicted_sector_win_count"], 1)
        self.assertEqual(row["predicted_sector_loss_count"], 1)
        summary = summarize_validation(rows)
        self.assertEqual(summary["validation_day_count"], 1)
        self.assertEqual(summary["predicted_sector_trade_count_total"], 2)

    def test_run_on_repo_data(self) -> None:
        data_root = REPO / "data" / "intraday_1m"
        if not data_root.is_dir():
            self.skipTest("intraday_1m missing")
        audit = MarketSectorHeatObservation(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
        )
        result = audit.run()
        self.assertGreater(result["intraday_day_count"], 0)
        self.assertGreater(result["sector_day_row_count"], 0)
        self.assertEqual(result["tomorrow_top3_row_count"] % TOP_SECTOR_COUNT, 0)
        self.assertTrue(result["constraints"]["entry_change_forbidden"])


if __name__ == "__main__":
    unittest.main()

"""Phase260A position exposure audit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.position_exposure_audit import (  # noqa: E402
    build_exposure_distribution_rows,
    build_feasibility_rows,
    enrich_entries,
    position_ratio,
    position_value_100,
    price_band_label,
    run_position_exposure_audit,
)


class TestPositionExposureAudit(unittest.TestCase):
    def test_position_value_and_ratio(self) -> None:
        self.assertEqual(position_value_100(5000.0), 500000.0)
        self.assertEqual(position_ratio(500000.0, 1_000_000), 0.5)

    def test_price_band_label(self) -> None:
        self.assertEqual(price_band_label(250), "<300")
        self.assertEqual(price_band_label(4500), "3000-5000")
        self.assertEqual(price_band_label(12000), "10000+")

    def test_build_exposure_distribution_rows(self) -> None:
        entries = enrich_entries(
            {
                "20260522": [
                    {"symbol": "1001.T", "entry_price": "5000", "pnl_yen_100": "100"},
                    {"symbol": "1002.T", "entry_price": "1000", "pnl_yen_100": "-50"},
                ]
            },
            overlap_days=["20260522"],
        )
        rows = build_exposure_distribution_rows(entries)
        self.assertEqual(len(rows), 5)
        row_1m = next(r for r in rows if r["equity_yen"] == 1_000_000)
        self.assertGreater(_float(row_1m["max_position_ratio"]), 0.4)

    def test_build_feasibility_rows(self) -> None:
        entries = enrich_entries(
            {"20260522": [{"symbol": "1001.T", "entry_price": "6000", "pnl_yen_100": "200"}]},
            overlap_days=["20260522"],
        )
        rows = build_feasibility_rows(entries)
        row_1m = next(r for r in rows if r["equity_yen"] == 1_000_000)
        self.assertEqual(row_1m["high_price_pct_gt_50"], 1.0)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase259_price_band_policy_shadow_summary.json").is_file():
            self.skipTest("phase259 summary missing")
        result = run_position_exposure_audit(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "260A-Position-Exposure-Audit")
        self.assertGreater(result["summary"]["entry_count"], 0)


def _float(v: object) -> float:
    return float(v) if v is not None else 0.0


if __name__ == "__main__":
    unittest.main()

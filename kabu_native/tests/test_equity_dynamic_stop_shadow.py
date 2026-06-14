"""Phase263 equity dynamic stop shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.equity_dynamic_stop_shadow import (  # noqa: E402
    EQUITY_LEVELS,
    FIXED_STOP_PCT,
    RISK_PCT_VALUES,
    aggregate_summary_rows,
    build_entry_level_rows,
    build_verdict,
    compute_stop_fields,
    run_equity_dynamic_stop_shadow,
    shadow_pnl_pct,
    shadow_pnl_yen,
)


class TestEquityDynamicStopShadow(unittest.TestCase):
    def test_compute_stop_fields_tightens_on_small_equity(self) -> None:
        stop = compute_stop_fields(entry_price=5000.0, shares=100, equity_yen=1_000_000, risk_pct=0.005)
        self.assertLess(stop["effective_stop_pct"], FIXED_STOP_PCT)
        self.assertTrue(stop["stop_tightened"])
        self.assertAlmostEqual(stop["risk_budget_yen"], 5000.0)

    def test_compute_stop_fields_caps_at_fixed(self) -> None:
        stop = compute_stop_fields(entry_price=500.0, shares=100, equity_yen=10_000_000, risk_pct=0.01)
        self.assertEqual(stop["effective_stop_pct"], FIXED_STOP_PCT)
        self.assertFalse(stop["stop_tightened"])

    def test_shadow_pnl_stops_on_mae(self) -> None:
        pct = shadow_pnl_pct(actual_pnl_pct=-0.5, mae_pct=-1.5, effective_stop_pct=0.8)
        self.assertEqual(pct, -0.8)
        yen = shadow_pnl_yen(
            entry_price=1000.0,
            shares=100,
            actual_pnl_pct=-0.5,
            mae_pct=-1.5,
            effective_stop_pct=0.8,
        )
        self.assertEqual(yen, -800.0)

    def test_build_entry_and_summary_rows(self) -> None:
        base = [
            {
                "day": "20260529",
                "symbol": "1001.T",
                "entry_price": 3000.0,
                "shares": 100,
                "pnl_yen_100_original": -1200.0,
                "actual_pnl_pct": -0.4,
                "mae_pct": -1.5,
            }
        ]
        entry_rows = build_entry_level_rows(base)
        self.assertEqual(len(entry_rows), len(EQUITY_LEVELS) * len(RISK_PCT_VALUES))
        summary = aggregate_summary_rows(entry_rows, base)
        self.assertEqual(len(summary), len(EQUITY_LEVELS) * (len(RISK_PCT_VALUES) + 1))

    def test_build_verdict_adoption_forbidden(self) -> None:
        base = [
            {
                "day": "20260529",
                "symbol": "1001.T",
                "entry_price": 3000.0,
                "shares": 100,
                "pnl_yen_100_original": 100.0,
                "actual_pnl_pct": 0.03,
                "mae_pct": -0.5,
            }
        ]
        entry_rows = build_entry_level_rows(base)
        summary = aggregate_summary_rows(entry_rows, base)
        verdict = build_verdict(summary_rows=summary, period_days=["20260529"], entry_count=1)
        self.assertTrue(verdict["adoption_forbidden"])

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        result = run_equity_dynamic_stop_shadow(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "263-Equity-Position-Based-Dynamic-Stop-Shadow")
        self.assertTrue(result["verdict"]["adoption_forbidden"])


if __name__ == "__main__":
    unittest.main()

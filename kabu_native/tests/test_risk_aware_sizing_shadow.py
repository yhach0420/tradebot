"""Phase261 risk-aware position sizing audit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.risk_aware_sizing_shadow import (  # noqa: E402
    SIZING_POLICIES,
    aggregate_policy_rows,
    build_entry_level_rows,
    compute_shares_for_policy,
    risk_per_100_shares_yen,
    run_risk_aware_sizing_audit,
    scale_policy_row,
    stop_distance_pct_from_trade,
)


class TestRiskAwareSizingShadow(unittest.TestCase):
    def test_stop_distance_and_risk(self) -> None:
        stop = stop_distance_pct_from_trade(mae_pct=-2.5, atr_proxy_pct=1.8)
        self.assertGreaterEqual(stop, 2.5)
        self.assertEqual(risk_per_100_shares_yen(1000.0, stop), round(1000.0 * 100 * stop / 100, 2))

    def test_risk_1pct_limits_shares(self) -> None:
        base = {
            "entry_price": 5000.0,
            "risk_per_100_shares_yen": 6000.0,
            "volatility_scale": 1.0,
        }
        shares, skipped_risk, skipped_min, _ = compute_shares_for_policy(
            base,
            equity_yen=1_000_000,
            policy="risk_1pct_equity",
        )
        self.assertEqual(shares, 100)
        self.assertFalse(skipped_min)

    def test_hybrid_takes_min_of_caps(self) -> None:
        base = {
            "entry_price": 1000.0,
            "risk_per_100_shares_yen": 1200.0,
            "volatility_scale": 1.0,
        }
        shares, _, skipped_min, _ = compute_shares_for_policy(
            base,
            equity_yen=1_000_000,
            policy="hybrid_equity30_risk1",
        )
        self.assertFalse(skipped_min)
        self.assertGreater(shares, 0)

    def test_aggregate_policy_rows(self) -> None:
        base = [{"day": "20260522", "symbol": "1001.T", "entry_price": 5000.0, "pnl_yen_100": 100.0,
                 "mae_pct": -1.5, "mfe_pct": 2.0, "intraday_range_pct": 3.0, "atr_proxy_pct": 1.2,
                 "recent_volatility_pct": 0.2, "stop_distance_pct": 1.5, "risk_per_100_shares_yen": 7500.0,
                 "position_value_100": 500000.0, "volatility_scale": 1.0}]
        rows = build_entry_level_rows(base)
        agg = aggregate_policy_rows(rows)
        self.assertEqual(len(agg), len(SIZING_POLICIES) * 4)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase260b_equity_position_sizing_summary.json").is_file():
            self.skipTest("phase260b summary missing")
        result = run_risk_aware_sizing_audit(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "261-Risk-Aware-Position-Sizing-Audit")
        self.assertTrue(result["verdict"]["adoption_forbidden"])


if __name__ == "__main__":
    unittest.main()

"""Phase260B equity position sizing shadow tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.equity_position_sizing_shadow import (  # noqa: E402
    SIZING_POLICIES,
    aggregate_policy_rows,
    build_entry_level_rows,
    compute_shares_shadow,
    run_equity_position_sizing_shadow,
    scale_entry_row,
)


class TestEquityPositionSizingShadow(unittest.TestCase):
    def test_fixed_100_shares(self) -> None:
        shares, skipped = compute_shares_shadow(8000.0, equity_yen=1_000_000, policy="fixed_100_shares")
        self.assertEqual(shares, 100)
        self.assertFalse(skipped)

    def test_max_position_30pct_skips_high_price_at_1m(self) -> None:
        shares, skipped = compute_shares_shadow(15000.0, equity_yen=1_000_000, policy="max_position_30pct")
        self.assertTrue(skipped)
        self.assertEqual(shares, 0)

    def test_min_lot_or_skip_uses_full_equity(self) -> None:
        shares, skipped = compute_shares_shadow(5000.0, equity_yen=1_000_000, policy="min_lot_or_skip")
        self.assertFalse(skipped)
        self.assertEqual(shares, 200)

    def test_scale_entry_row_pnl(self) -> None:
        row = scale_entry_row(
            day="20260522",
            symbol="1001.T",
            entry_price=1000.0,
            pnl_yen_100=100.0,
            equity_yen=1_000_000,
            policy="max_position_30pct",
        )
        self.assertGreater(row["shares_shadow"], 100)
        self.assertGreater(row["pnl_yen_scaled"], 100.0)

    def test_aggregate_policy_rows(self) -> None:
        entries = [{"day": "20260522", "symbol": "1001.T", "entry_price": 5000.0, "pnl_yen_100": 200.0}]
        rows = build_entry_level_rows(entries)
        agg = aggregate_policy_rows(rows)
        self.assertEqual(len(agg), len(SIZING_POLICIES) * 4)

    def test_run_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase260a_position_exposure_audit_summary.json").is_file():
            self.skipTest("phase260a summary missing")
        result = run_equity_position_sizing_shadow(repo_root=REPO, reports_dir=reports)
        self.assertEqual(result["phase"], "260B-Equity-Aware-Position-Sizing-Shadow")
        self.assertTrue(result["verdict"]["adoption_forbidden"])


if __name__ == "__main__":
    unittest.main()

"""Phase262 risk sizing forward shadow tests."""

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
    FORWARD_SIZING_POLICIES,
    aggregate_forward_summary_rows,
    build_forward_entry_rows,
)
from research.risk_sizing_forward_shadow_logger import (  # noqa: E402
    compute_forward_summary,
    run_forward_shadow_logger,
)
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.risk_sizing_forward_shadow_auto import (  # noqa: E402
    infer_validation_day,
    run_risk_sizing_forward_shadow_auto,
)


class TestRiskSizingForwardShadow(unittest.TestCase):
    def test_build_forward_entry_rows(self) -> None:
        base = [
            {
                "day": "20260522",
                "symbol": "1001.T",
                "entry_price": 5000.0,
                "pnl_yen_100": 100.0,
                "risk_per_100_shares_yen": 6000.0,
                "volatility_scale": 1.0,
                "position_value_100": 500000.0,
            }
        ]
        rows = build_forward_entry_rows(base)
        self.assertEqual(len(rows), len(FORWARD_SIZING_POLICIES) * 4)

    def test_compute_forward_summary_adopt_blocked(self) -> None:
        entry_rows = build_forward_entry_rows(
            [
                {
                    "day": "20260522",
                    "symbol": "1001.T",
                    "entry_price": 1000.0,
                    "pnl_yen_100": 50.0,
                    "risk_per_100_shares_yen": 1200.0,
                    "volatility_scale": 1.0,
                    "position_value_100": 100000.0,
                }
            ]
        )
        summary_rows = aggregate_forward_summary_rows(entry_rows)
        summary = compute_forward_summary(entry_rows, summary_rows)
        self.assertTrue(summary["adopt_not_allowed"])

    def test_format_research_shadow_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "risk_sizing_forward_shadow": {
                    "trade_overlap_days": 4,
                    "best_policy": "risk_1pct_equity",
                    "adopt_not_allowed": True,
                    "status": "success",
                }
            }
        )
        self.assertIn("RiskAware Sizing Shadow:", lines[0])
        self.assertTrue(any("best_policy=risk_1pct_equity" in line for line in lines))

    def test_infer_validation_day(self) -> None:
        day = infer_validation_day(output_dir=Path("kabu_native/results/small_paper/20260525/live_full_session_120000"))
        self.assertEqual(day, "20260525")

    def test_run_forward_logger_backfill(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase261_entry_level_risk_sizing.csv").is_file():
            self.skipTest("phase261 entry csv missing")
        result = run_forward_shadow_logger(
            repo_root=REPO,
            reports_dir=reports,
            backfill_phase261=True,
        )
        self.assertEqual(result["phase"], "262-Risk-Aware-Sizing-Forward-Shadow")
        self.assertGreater(len(result.get("_entry_rows") or []), 0)

    def test_run_auto_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase261_entry_level_risk_sizing.csv").is_file():
            self.skipTest("phase261 entry csv missing")
        block = run_risk_sizing_forward_shadow_auto(repo_root=REPO, day="20260525", reports_dir=reports)
        self.assertIn(block["status"], ("success", "skipped", "warning"))
        self.assertEqual(block["day"], "20260525")


if __name__ == "__main__":
    unittest.main()

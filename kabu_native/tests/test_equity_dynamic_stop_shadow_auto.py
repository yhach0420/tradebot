"""Phase266 equity dynamic stop shadow auto tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.equity_dynamic_stop_shadow import PERIOD_START, best_policy_for_equity  # noqa: E402
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.equity_dynamic_stop_shadow_auto import (  # noqa: E402
    infer_validation_day,
    run_equity_dynamic_stop_shadow_auto,
)


class TestEquityDynamicStopShadowAuto(unittest.TestCase):
    def test_infer_validation_day(self) -> None:
        day = infer_validation_day(output_dir=Path("kabu_native/results/small_paper/20260612/live_session_075327"))
        self.assertEqual(day, "20260612")

    def test_skipped_before_period(self) -> None:
        block = run_equity_dynamic_stop_shadow_auto(repo_root=REPO, day="20260525")
        self.assertEqual(block["status"], "skipped_before_period")
        self.assertEqual(block["day"], "20260525")

    def test_format_research_shadow_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "equity_dynamic_stop_shadow": {
                    "days": 9,
                    "best_policy_1p5m": "dynamic_stop_risk_1p0",
                    "best_policy_5m": "dynamic_stop_risk_0p5",
                    "adopt_not_allowed": True,
                    "status": "success",
                }
            }
        )
        self.assertIn("Equity Dynamic Stop Shadow:", lines)
        self.assertTrue(any("best_policy_5m=dynamic_stop_risk_0p5" in line for line in lines))

    def test_best_policy_for_equity(self) -> None:
        rows = [
            {
                "equity_yen": 1_500_000,
                "risk_pct": 0.01,
                "stop_policy": "dynamic_stop_risk_1p0",
                "delta_vs_fixed_stop": 100.0,
            },
            {
                "equity_yen": 1_500_000,
                "risk_pct": 0.005,
                "stop_policy": "dynamic_stop_risk_0p5",
                "delta_vs_fixed_stop": 50.0,
            },
        ]
        self.assertEqual(best_policy_for_equity(rows, equity_yen=1_500_000), "dynamic_stop_risk_1p0")

    def test_run_auto_never_raises(self) -> None:
        with patch(
            "research.equity_dynamic_stop_shadow.EquityDynamicStopShadow.run",
            side_effect=RuntimeError("boom"),
        ):
            block = run_equity_dynamic_stop_shadow_auto(repo_root=REPO, day=PERIOD_START)
        self.assertEqual(block["status"], "warning")
        self.assertIn("boom", block.get("warning", ""))

    def test_run_auto_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        block = run_equity_dynamic_stop_shadow_auto(repo_root=REPO, day="20260612", reports_dir=reports)
        self.assertIn(block["status"], ("success", "skipped", "warning", "skipped_no_period_trades"))
        if block["status"] == "success":
            self.assertGreater(int(block.get("days") or 0), 0)


if __name__ == "__main__":
    unittest.main()

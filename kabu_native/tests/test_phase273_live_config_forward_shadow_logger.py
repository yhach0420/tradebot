"""Phase273 live config forward shadow tests."""

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

from research.equity_curve_shadow import PERIOD_START  # noqa: E402
from research.phase273_live_config_forward_shadow_logger import (  # noqa: E402
    LIVE_CONFIG_CANDIDATES,
    compute_candidate_summary,
    resolve_current_recommendation,
    run_forward_shadow_logger,
)
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.live_config_forward_shadow_auto import (  # noqa: E402
    infer_validation_day,
    run_live_config_forward_shadow_auto,
)


class TestLiveConfigForwardShadow(unittest.TestCase):
    def test_infer_validation_day(self) -> None:
        day = infer_validation_day(
            output_dir=Path("kabu_native/results/small_paper/20260612/live_session_075327")
        )
        self.assertEqual(day, "20260612")

    def test_adopt_blocked_before_min_days(self) -> None:
        summary = compute_candidate_summary(
            {
                "final_equity": 1_700_000.0,
                "total_return_pct": 13.3,
                "max_drawdown_pct": 5.0,
                "days_below_50pct": 0,
                "accepted_trade_count": 100,
                "rejected_trade_count": 50,
                "profit_factor": 1.2,
                "win_rate": 0.52,
            },
            candidate=LIVE_CONFIG_CANDIDATES[0],
            period_days=["20260529"],
            trades=[],
        )
        self.assertTrue(summary["adopt_not_allowed"])
        self.assertEqual(summary["verdict"], "observe")

    def test_caution_on_high_drawdown(self) -> None:
        summary = compute_candidate_summary(
            {
                "final_equity": 1_700_000.0,
                "total_return_pct": 13.3,
                "max_drawdown_pct": 25.0,
                "days_below_50pct": 0,
                "accepted_trade_count": 100,
                "rejected_trade_count": 50,
                "profit_factor": 1.2,
                "win_rate": 0.52,
            },
            candidate=LIVE_CONFIG_CANDIDATES[0],
            period_days=[f"2026053{i}" for i in range(10)],
            trades=[],
        )
        self.assertTrue(summary["caution"])
        self.assertFalse(summary["adopt_not_allowed"])
        self.assertEqual(summary["verdict"], "caution")

    def test_resolve_current_recommendation(self) -> None:
        summaries = [
            {
                "candidate_key": "live_start_candidate_1500k",
                "adopt_not_allowed": False,
            },
            {
                "candidate_key": "scale_candidate_2000k_plus",
                "adopt_not_allowed": True,
            },
            {
                "candidate_key": "scale_candidate_3000k",
                "adopt_not_allowed": True,
            },
        ]
        self.assertEqual(
            resolve_current_recommendation(summaries),
            "live_start_candidate_1500k",
        )

    def test_format_research_shadow_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "live_config_forward_shadow": {
                    "day_count": 9,
                    "candidate_1500k": {
                        "final_equity": 1650270.0,
                        "max_drawdown_pct": 5.87,
                        "verdict": "observe",
                    },
                    "candidate_2000k": {
                        "final_equity": 2289964.39,
                        "max_drawdown_pct": 5.56,
                        "verdict": "observe",
                    },
                    "current_recommendation": "live_start_candidate_1500k",
                    "status": "success",
                }
            }
        )
        self.assertIn("LiveConfig Shadow:", lines)
        self.assertTrue(any("1500k:" in line for line in lines))
        self.assertTrue(any("current=live_start_candidate_1500k" in line for line in lines))

    def test_skipped_before_period(self) -> None:
        block = run_live_config_forward_shadow_auto(repo_root=REPO, day="20260525")
        self.assertEqual(block["status"], "skipped_before_period")

    def test_run_auto_never_raises(self) -> None:
        with patch(
            "research.phase273_live_config_forward_shadow_logger.LiveConfigForwardShadowLogger.run",
            side_effect=RuntimeError("boom"),
        ):
            block = run_live_config_forward_shadow_auto(repo_root=REPO, day=PERIOD_START)
        self.assertEqual(block["status"], "warning")
        self.assertIn("boom", block.get("warning", ""))

    def test_run_forward_logger_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        result = run_forward_shadow_logger(repo_root=REPO, reports_dir=reports, day="20260612")
        self.assertEqual(result["phase"], "273-Forward-Live-Configuration-Shadow-Logger")
        self.assertEqual(len(result.get("_daily_rows") or []), 27)
        self.assertGreater(len(result.get("_trade_events") or []), 0)
        summary = result.get("forward_summary") or {}
        self.assertEqual(len(summary.get("candidates") or []), 3)
        c1500 = (summary.get("candidates") or [])[0]
        self.assertEqual(c1500.get("candidate_key"), "live_start_candidate_1500k")
        self.assertEqual(c1500.get("cap"), 3)

    def test_run_auto_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        block = run_live_config_forward_shadow_auto(repo_root=REPO, day="20260612", reports_dir=reports)
        self.assertIn(block["status"], ("success", "skipped", "warning"))
        if block["status"] == "success":
            self.assertGreater(int(block.get("day_count") or 0), 0)


if __name__ == "__main__":
    unittest.main()

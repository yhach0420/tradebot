"""Phase256 Sector Heat Forward Shadow auto-run tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.sector_heat_forward_shadow_auto import (  # noqa: E402
    _resolve_status,
    infer_validation_day,
    run_sector_heat_forward_shadow_auto,
)


class TestSectorHeatForwardShadowAuto(unittest.TestCase):
    def test_infer_validation_day_from_output_dir(self) -> None:
        day = infer_validation_day(output_dir=Path("kabu_native/results/small_paper/20260615/live_full_session_120000"))
        self.assertEqual(day, "20260615")

    def test_infer_validation_day_explicit(self) -> None:
        self.assertEqual(infer_validation_day(day="20260525"), "20260525")

    def test_resolve_status(self) -> None:
        self.assertEqual(
            _resolve_status(last_run={"universe_status": "logged_4_patterns"}, error=None),
            "success",
        )
        self.assertEqual(
            _resolve_status(last_run={"trade_status": "skipped_no_structural_trades"}, error=None),
            "skipped",
        )
        self.assertEqual(_resolve_status(last_run={}, error="boom"), "warning")

    def test_format_research_shadow_daily_summary_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "sector_heat_forward_shadow": {
                    "trade_overlap_days": 5,
                    "adopt_not_allowed": True,
                    "status": "success",
                }
            }
        )
        self.assertIn("SectorHeat Forward Shadow:", lines[0])
        self.assertTrue(any("trade_overlap_days=5" in line for line in lines))
        self.assertTrue(any("adopt_not_allowed=True" in line for line in lines))

    def test_run_auto_never_raises(self) -> None:
        with patch(
            "research.market_sector_heat_forward_shadow_logger.MarketSectorHeatForwardShadowLogger.run",
            side_effect=RuntimeError("boom"),
        ):
            block = run_sector_heat_forward_shadow_auto(repo_root=REPO, day="20260615")
        self.assertEqual(block["status"], "warning")
        self.assertIn("boom", block.get("warning", ""))

    def test_run_auto_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase253_sector_heat_negative_filter_summary.json").is_file():
            self.skipTest("phase253 summary missing")
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "20260525" / "live_full_session_120000"
            session_dir.mkdir(parents=True)
            block = run_sector_heat_forward_shadow_auto(
                repo_root=REPO,
                output_dir=session_dir,
                day="20260525",
                reports_dir=reports,
            )
        self.assertIn(block["status"], ("success", "skipped", "warning"))
        self.assertEqual(block["day"], "20260525")
        self.assertIsNotNone(block.get("trade_overlap_days"))


if __name__ == "__main__":
    unittest.main()

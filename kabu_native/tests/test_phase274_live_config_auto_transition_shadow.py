"""Phase274 live config auto transition shadow tests."""

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
from research.phase274_live_config_auto_transition_shadow import (  # noqa: E402
    STARTING_EQUITY,
    TRANSITION_EQUITY,
    compute_adoption_verdict,
    resolve_policy_band,
    run_transition_shadow,
    simulate_auto_transition,
)
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402
from small_paper.live_config_transition_shadow_auto import (  # noqa: E402
    infer_validation_day,
    run_live_config_transition_shadow_auto,
)


class TestLiveConfigAutoTransitionShadow(unittest.TestCase):
    def test_resolve_policy_band(self) -> None:
        low = resolve_policy_band(1_900_000)
        self.assertEqual(low["active_policy_band"], "1500k")
        self.assertEqual(low["cap"], 3)
        self.assertEqual(low["stop_policy"], "fixed_stop_1p2")
        high = resolve_policy_band(TRANSITION_EQUITY)
        self.assertEqual(high["active_policy_band"], "2000k+")
        self.assertEqual(high["cap"], 5)

    def test_adopt_blocked_before_min_days(self) -> None:
        verdict = compute_adoption_verdict(
            metrics={"final_equity": 1_700_000, "days_below_50pct": 0, "max_drawdown_pct": 5.0},
            day_count=9,
        )
        self.assertTrue(verdict["adopt_not_allowed"])
        self.assertEqual(verdict["adoption_verdict"], "observe")

    def test_infer_validation_day(self) -> None:
        day = infer_validation_day(
            output_dir=Path("kabu_native/results/small_paper/20260612/live_session_075327")
        )
        self.assertEqual(day, "20260612")

    def test_format_research_shadow_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "live_config_transition_shadow": {
                    "current_equity": 1650270.0,
                    "active_policy_band": "1500k",
                    "cap_used": 3,
                    "stop_policy_used": "fixed_stop_1p2",
                    "transition_to_2000k": False,
                    "status": "success",
                }
            }
        )
        self.assertIn("LiveConfig Transition Shadow:", lines)
        self.assertTrue(any("band=1500k" in line for line in lines))
        self.assertTrue(any("transition_to_2000k=False" in line for line in lines))

    def test_skipped_before_period(self) -> None:
        block = run_live_config_transition_shadow_auto(repo_root=REPO, day="20260525")
        self.assertEqual(block["status"], "skipped_before_period")

    def test_run_auto_never_raises(self) -> None:
        with patch(
            "research.phase274_live_config_auto_transition_shadow.LiveConfigAutoTransitionShadow.run",
            side_effect=RuntimeError("boom"),
        ):
            block = run_live_config_transition_shadow_auto(repo_root=REPO, day=PERIOD_START)
        self.assertEqual(block["status"], "warning")

    def test_simulate_on_repo_trades(self) -> None:
        from research.equity_curve_shadow import load_period_trades

        trades, _ = load_period_trades(REPO, period_start=PERIOD_START)
        if not trades:
            self.skipTest("no period trades")
        sim = simulate_auto_transition(trades)
        self.assertEqual(sim.get("starting_equity"), STARTING_EQUITY)
        self.assertGreater(float(sim.get("final_equity") or 0), STARTING_EQUITY)
        self.assertGreater(len(sim.get("_equity_curve") or []), 0)

    def test_run_transition_shadow_on_repo(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        result = run_transition_shadow(repo_root=REPO, reports_dir=reports, day="20260612")
        self.assertEqual(result["phase"], "274-Live-Config-Auto-Transition-Shadow")
        summary = result.get("transition_summary") or {}
        self.assertEqual(summary.get("starting_equity"), STARTING_EQUITY)
        self.assertIn("adoption_verdict", summary)


if __name__ == "__main__":
    unittest.main()

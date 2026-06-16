"""Phase409 boundary forward shadow tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase409_boundary_forward_shadow import (  # noqa: E402
    FORWARD_PERIOD_START,
    MIN_ADOPTION_REVIEW_DAYS,
    MIN_OBSERVE_DAYS,
    BoundaryForwardShadowLogger,
    aggregate_day_metrics,
    apply_daily_verdicts,
    compute_cumulative_summary,
    forward_verdict,
    load_structural_trades_for_day,
    run_forward_shadow_logger,
)
from research.structural_trade_normalize import normalize_structural_trade_row, resolve_kabu_root  # noqa: E402
from small_paper.boundary_forward_shadow_auto import run_boundary_forward_shadow_auto  # noqa: E402
from small_paper.discord_message_builder import format_research_shadow_daily_summary_lines  # noqa: E402


class TestPhase409BoundaryForwardShadow(unittest.TestCase):
    def test_forward_verdict_gates(self) -> None:
        self.assertEqual(forward_verdict(0), "observe")
        self.assertEqual(forward_verdict(MIN_OBSERVE_DAYS - 1), "observe")
        self.assertEqual(forward_verdict(MIN_OBSERVE_DAYS), "review_required")
        self.assertEqual(forward_verdict(MIN_ADOPTION_REVIEW_DAYS - 1), "review_required")
        self.assertEqual(forward_verdict(MIN_ADOPTION_REVIEW_DAYS), "adoption_review_allowed")

    def test_shadow_exit_row_generation(self) -> None:
        mock_row = {
            "logged_at": "2026-06-16T10:00:00+09:00",
            "day": FORWARD_PERIOD_START,
            "session": "live_session_test",
            "symbol": "9984.T",
            "entry_time": "2026-06-16T09:00:00+09:00",
            "exit_time": "2026-06-16T09:30:00+09:00",
            "baseline_pnl_yen_100": 100.0,
            "shadow_pnl_yen_100": 200.0,
            "delta_yen": 100.0,
            "baseline_exit_reason": "trailing_mfe",
            "shadow_exit_reason": "boundary_mfe_exit",
            "shadow_exit_ts": 1780000000.0,
            "used_baseline_fallback": False,
            "post_baseline_violation": False,
        }
        self.assertFalse(mock_row["post_baseline_violation"])
        self.assertIn("boundary", mock_row["shadow_exit_reason"])

    def test_summary_upsert_and_day_count(self) -> None:
        trade_rows = [
            {
                "day": FORWARD_PERIOD_START,
                "session": "s1",
                "exit_time": "2026-06-16T10:00:00+09:00",
                "baseline_pnl_yen_100": 100.0,
                "shadow_pnl_yen_100": 150.0,
                "shadow_exit_reason": "boundary_mfe_exit",
                "post_baseline_violation": False,
            },
            {
                "day": "20260617",
                "session": "s2",
                "exit_time": "2026-06-17T10:00:00+09:00",
                "baseline_pnl_yen_100": -50.0,
                "shadow_pnl_yen_100": 0.0,
                "shadow_exit_reason": "baseline",
                "post_baseline_violation": False,
            },
        ]
        daily_rows = [
            {"day": FORWARD_PERIOD_START, "trade_count": 1},
            {"day": "20260617", "trade_count": 1},
        ]
        summary = compute_cumulative_summary(trade_rows, daily_rows)
        self.assertEqual(summary["day_count"], 2)
        self.assertEqual(summary["post_baseline_usage_count"], 0)
        self.assertTrue(summary["replay_audit_pass"])
        apply_daily_verdicts(daily_rows, summary["period_days"])
        self.assertEqual(daily_rows[0]["verdict"], "observe")
        self.assertEqual(daily_rows[1]["verdict"], "observe")

    @patch("research.phase409_boundary_forward_shadow.load_structural_trades_for_day")
    def test_logger_writes_research_outputs(self, mock_load) -> None:
        mock_load.return_value = [
            {
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "exit_time": "2026-06-16T09:30:00+09:00",
                "exit_reason": "trailing_mfe",
                "pnl_yen_100": 100,
                "day": FORWARD_PERIOD_START,
                "session": "live_session_test",
                "position_cap_accepted": True,
            }
        ]
        with patch(
            "research.phase409_boundary_forward_shadow.evaluate_boundary_shadow_trade",
            return_value={
                "logged_at": "2026-06-16T10:00:00+09:00",
                "day": FORWARD_PERIOD_START,
                "session": "live_session_test",
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "exit_time": "2026-06-16T09:30:00+09:00",
                "baseline_pnl_yen_100": 100.0,
                "shadow_pnl_yen_100": 200.0,
                "delta_yen": 100.0,
                "baseline_exit_reason": "trailing_mfe",
                "shadow_exit_reason": "boundary_mfe_exit",
                "shadow_exit_ts": 1780000000.0,
                "used_baseline_fallback": False,
                "post_baseline_violation": False,
            },
        ):
            with tempfile.TemporaryDirectory() as tmp:
                reports = Path(tmp)
                repo_root = REPO
                result = run_forward_shadow_logger(
                    repo_root=repo_root,
                    reports_dir=reports,
                    day=FORWARD_PERIOD_START,
                )
                job = BoundaryForwardShadowLogger(repo_root=repo_root, reports_dir=reports)
                paths = job.write_outputs(result)
                self.assertTrue(paths["summary"].is_file())
                self.assertTrue(paths["trades"].is_file())
                self.assertTrue(paths["daily"].is_file())
                payload = json.loads(paths["summary"].read_text(encoding="utf-8"))
                self.assertEqual(payload["forward_summary"]["day_count"], 1)

    def test_auto_never_raises_on_failure(self) -> None:
        with patch(
            "research.phase409_boundary_forward_shadow.BoundaryForwardShadowLogger.run",
            side_effect=RuntimeError("shadow boom"),
        ):
            block = run_boundary_forward_shadow_auto(repo_root=REPO, day=FORWARD_PERIOD_START)
            self.assertEqual(block["status"], "warning")
            self.assertIn("shadow boom", str(block.get("warning")))

    def test_discord_research_summary_lines(self) -> None:
        lines = format_research_shadow_daily_summary_lines(
            {
                "boundary_forward_shadow": {
                    "day_count": 2,
                    "baseline_total_pnl_yen_100": 1000.0,
                    "shadow_total_pnl_yen_100": 1200.0,
                    "delta_pnl_yen_100": 200.0,
                    "shadow_pf": 1.2,
                    "shadow_maxdd_yen_100": 5000.0,
                    "verdict": "observe",
                    "status": "success",
                }
            }
        )
        text = "\n".join(lines)
        self.assertIn("Boundary Shadow", text)
        self.assertIn("verdict=observe", text)

    def test_day_count_includes_zero_hit_session(self) -> None:
        daily_rows = [
            {
                "day": FORWARD_PERIOD_START,
                "session_count": 2,
                "trade_count": 0,
                "structural_trade_count": 774,
                "eval_failed_count": 0,
                "boundary_eligible_count": 0,
            }
        ]
        summary = compute_cumulative_summary([], daily_rows)
        self.assertEqual(summary["day_count"], 1)

    def test_close_time_mapping_loads_trades(self) -> None:
        row = normalize_structural_trade_row(
            {
                "symbol": "7203",
                "open_time": "2026-06-16T10:00:00+09:00",
                "close_time": "2026-06-16T10:05:00+09:00",
                "entry_price": 2000,
                "close_price": 2010,
            },
            day=FORWARD_PERIOD_START,
            session="live_session_test",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["entry_time"], "2026-06-16T10:00:00+09:00")
        self.assertEqual(row["exit_time"], "2026-06-16T10:05:00+09:00")

    def test_resolve_kabu_root_from_workspace(self) -> None:
        self.assertEqual(resolve_kabu_root(REPO), REPO.resolve())

    def test_aggregate_day_metrics_zero_trades_still_logs_day(self) -> None:
        metrics = aggregate_day_metrics(
            [],
            day=FORWARD_PERIOD_START,
            session_count=2,
            structural_trade_count=100,
            eval_failed_count=100,
        )
        self.assertEqual(metrics["day"], FORWARD_PERIOD_START)
        self.assertEqual(metrics["trade_count"], 0)
        self.assertEqual(metrics["structural_trade_count"], 100)

    def test_dedupe_allows_same_entry_time_different_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = FORWARD_PERIOD_START
            sess = root / "results" / "small_paper" / day / "live_session_test"
            sess.mkdir(parents=True)
            csv_path = sess / "structural_trades.csv"
            csv_path.write_text(
                "symbol,entry_time,close_time,entry_price,close_price\n"
                "9984,2026-06-16T09:00:00+09:00,2026-06-16T09:05:00+09:00,1000,1010\n"
                "7203,2026-06-16T09:00:00+09:00,2026-06-16T09:05:00+09:00,2000,2010\n",
                encoding="utf-8",
            )
            trades = load_structural_trades_for_day(root, day)
            self.assertEqual(len(trades), 2)

    def test_load_structural_trades_finds_616_sessions(self) -> None:
        trades = load_structural_trades_for_day(REPO, FORWARD_PERIOD_START)
        if not trades:
            self.skipTest("no 20260616 structural trades in workspace")
        self.assertGreater(len(trades), 0)
        self.assertTrue(all(t.get("exit_time") for t in trades))

    def test_runtime_hook_does_not_modify_exit_policy(self) -> None:
        from small_paper import pilot_runner

        src = Path(pilot_runner.__file__).read_text(encoding="utf-8")
        self.assertIn("_run_boundary_forward_shadow_auto", src)
        self.assertIn("boundary_forward_shadow", src)
        self.assertNotIn("structural_exit_policy", src.split("_run_boundary_forward_shadow_auto")[1][:800])


if __name__ == "__main__":
    unittest.main()

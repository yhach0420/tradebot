"""Phase642: daily runner verdict policy — completed_with_warnings vs hard fail."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from runner.am_pm_daily_runner import (  # noqa: E402
    DailyRunnerOptions,
    _apply_pilot_verdict_policy,
    _pilot_completed_with_warnings,
    _pilot_failed_hard,
    _run_daily_runner_body,
    make_state,
)


def _write_completed_summary(session_dir: Path, *, accepted: int = 43) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "small_paper_summary.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-01T09:33:10+09:00",
                "ended_at": "2026-07-01T11:24:03+09:00",
                "stop_reason": "completed",
                "push_messages": 434578,
                "gate_evaluations": 40210,
                "accepted_count": accepted,
                "rejected_count": 40144,
                "runtime_sec": 6651.9,
            }
        ),
        encoding="utf-8",
    )


def _repo_layout(tmp_path: Path, day: str = "20260701") -> tuple[Path, Path, str]:
    repo = tmp_path
    session = repo / "kabu_native" / "results" / "small_paper" / day / "live_session_080616"
    rel = f"kabu_native/results/small_paper/{day}/live_session_080616"
    return repo, session, rel


class Phase642VerdictPolicyTests(unittest.TestCase):
    def test_20260701_like_exit1_completed_summary_not_hard_failed(self) -> None:
        with self.subTest("completed summary"):
            import tempfile

            tmp = Path(tempfile.mkdtemp())
            try:
                repo, session, rel = _repo_layout(tmp)
                _write_completed_summary(session, accepted=43)
                live = {
                    "exit_code": 1,
                    "session_dir": rel,
                    "summary_found": True,
                    "stderr_tail": "Safety check failed: something",
                }
                ok, details = _pilot_completed_with_warnings(repo, live)
                self.assertTrue(ok)
                self.assertEqual(details.get("stop_reason"), "completed")
                self.assertEqual(details.get("accepted_count"), 43)
                self.assertFalse(_pilot_failed_hard(live, repo_root=repo))
            finally:
                import shutil

                shutil.rmtree(tmp, ignore_errors=True)

    def test_crash_exit1_no_summary_is_hard_failed(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo, _, _ = _repo_layout(tmp)
            live = {"exit_code": 1, "session_dir": None, "summary_found": False}
            self.assertFalse(_pilot_completed_with_warnings(repo, live)[0])
            self.assertTrue(_pilot_failed_hard(live, repo_root=repo))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_exit1_wrong_stop_reason_is_hard_failed(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo, session, rel = _repo_layout(tmp)
            session.mkdir(parents=True)
            (session / "small_paper_summary.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-01T09:00:00+09:00",
                        "ended_at": "2026-07-01T09:03:00+09:00",
                        "stop_reason": "keyboard_interrupt",
                        "push_messages": 10,
                        "gate_evaluations": 1,
                    }
                ),
                encoding="utf-8",
            )
            live = {"exit_code": 1, "session_dir": rel}
            self.assertFalse(_pilot_completed_with_warnings(repo, live)[0])
            self.assertTrue(_pilot_failed_hard(live, repo_root=repo))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_pilot_verdict_policy_sets_warning_notes(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo, session, rel = _repo_layout(tmp)
            _write_completed_summary(session)
            state = make_state(repo, repo / "kabu_native", DailyRunnerOptions(day_stamp="20260701"))
            live: dict = {
                "exit_code": 1,
                "session_dir": rel,
                "stderr_last_20_lines": ["line1", "line2"],
            }
            _apply_pilot_verdict_policy(state, live)
            self.assertEqual(live["pilot_verdict"], "completed_with_warnings")
            self.assertTrue(live["pilot_ok"])
            self.assertTrue(any("pilot_exit_code=1" in n for n in live.get("warning_notes") or []))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_am_completed_with_warnings_continues_to_pm(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo, session, rel = _repo_layout(tmp, day="20260521")
            _write_completed_summary(session)
            am_live = {
                "exit_code": 1,
                "session_dir": rel,
                "summary_found": True,
                "pilot_verdict": "completed_with_warnings",
                "pilot_ok": True,
                "ok": True,
                "warning_notes": ["pilot_exit_code=1"],
                "pilot_verdict_details": {"accepted_count": 43, "stop_reason": "completed"},
            }
            state = make_state(
                repo,
                repo / "kabu_native",
                DailyRunnerOptions(
                    day_stamp="20260521",
                    skip_safety=True,
                    skip_kabu=True,
                    dry_run_only=False,
                    skip_pm=False,
                ),
            )
            with patch("runner.am_pm_daily_runner.preflight", return_value=True):
                with patch(
                    "runner.am_pm_daily_runner.build_am_universe",
                    return_value={
                        "ok": True,
                        "am_csv": "kabu_native/results/reports/universe_core10_dynamic40_am_20260521.csv",
                    },
                ):
                    with patch(
                        "runner.am_pm_daily_runner.notify_screening_universe_discord",
                        return_value={"skipped": True},
                    ):
                        with patch("runner.am_pm_daily_runner.run_pilot_session", return_value=am_live):
                            with patch(
                                "runner.am_pm_daily_runner.wait_until_hhmm",
                                return_value={"skipped": True},
                            ):
                                with patch("runner.am_pm_daily_runner.build_pm_universe") as pm_build:
                                    pm_build.return_value = {"ok": False, "error": "stop_here"}
                                    rc = _run_daily_runner_body(state)

            self.assertEqual(rc, 2)
            self.assertNotEqual(state.verdict, "am_failed")
            self.assertEqual(state.stopped_reason, "pm_universe")
            pm_build.assert_called_once()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_preflight_blocked_unchanged(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            state = make_state(
                tmp,
                tmp / "kabu_native",
                DailyRunnerOptions(day_stamp="20260630", skip_safety=True),
            )
            with patch("runner.am_pm_daily_runner.preflight", return_value=False):
                state.verdict = "preflight_blocked"
                rc = _run_daily_runner_body(state)
            self.assertEqual(rc, 2)
            self.assertEqual(state.verdict, "preflight_blocked")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

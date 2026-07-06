"""Phase641b: pilot subprocess stdout/stderr logging for daily runner diagnostics."""

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
    _persist_pilot_subprocess_artifacts,
    _record_pilot_hard_failure,
    build_summary_payload,
    make_state,
    run_pilot_session,
)
from runner.pilot_subprocess_logging import (  # noqa: E402
    PILOT_STDERR_LOG,
    PILOT_STDOUT_LOG,
    TAIL_LINE_COUNT,
    build_warning_log_notes,
    format_pilot_exit_display,
    parse_traceback_fields,
    persist_pilot_subprocess_logs,
    tail_lines,
)
from small_paper.discord_message_builder import format_runtime_health_lines  # noqa: E402

TRACEBACK_SAMPLE = """\
Traceback (most recent call last):
  File "pilot.py", line 10, in main
    raise RuntimeError("boom")
RuntimeError: boom
"""


class Phase641bSubprocessLoggingTests(unittest.TestCase):
    def test_exit0_stdout_only_persisted(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            session = tmp / "live_session_080000"
            stdout = "pilot started\npilot finished\n"
            meta = persist_pilot_subprocess_logs(session, stdout=stdout, stderr="")
            self.assertTrue((session / PILOT_STDOUT_LOG).is_file())
            self.assertTrue((session / PILOT_STDERR_LOG).is_file())
            self.assertEqual((session / PILOT_STDOUT_LOG).read_text(encoding="utf-8"), stdout)
            self.assertEqual(meta["stdout_last_20_lines"], stdout.strip().splitlines())
            self.assertEqual(meta["stderr_last_20_lines"], [])
            self.assertEqual(format_pilot_exit_display(exit_code=0, pilot_verdict="success"), "0")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_exit1_warning_stderr_summary_in_notes(self) -> None:
        live = {
            "exit_code": 1,
            "stderr_last_20_lines": ["WARN: safety tail", "done"],
            "stdout_last_20_lines": ["ok line"],
        }
        notes = build_warning_log_notes(live)
        self.assertTrue(any("pilot_exit_code=1" in n for n in notes))
        self.assertTrue(any(n.startswith("stderr_summary:") for n in notes))
        self.assertTrue(any(n.startswith("stdout_summary:") for n in notes))

    def test_exit1_crash_traceback_parsed(self) -> None:
        tb = parse_traceback_fields(TRACEBACK_SAMPLE)
        self.assertIn("RuntimeError", tb["first_exception"])
        self.assertIn("Traceback", tb["first_traceback"])
        self.assertIn("RuntimeError: boom", tb["first_error_line"])

    def test_huge_streams_full_file_tail_in_summary(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            session = tmp / "live_session_090000"
            stdout = "\n".join(f"stdout-{i}" for i in range(100))
            stderr = "\n".join(f"stderr-{i}" for i in range(100))
            meta = persist_pilot_subprocess_logs(session, stdout=stdout, stderr=stderr)
            self.assertEqual(len((session / PILOT_STDOUT_LOG).read_text(encoding="utf-8").splitlines()), 100)
            self.assertEqual(len((session / PILOT_STDERR_LOG).read_text(encoding="utf-8").splitlines()), 100)
            self.assertEqual(len(meta["stdout_last_20_lines"]), TAIL_LINE_COUNT)
            self.assertEqual(len(meta["stderr_last_20_lines"]), TAIL_LINE_COUNT)
            self.assertEqual(meta["stdout_last_20_lines"][0], "stdout-80")
            self.assertEqual(meta["stderr_last_20_lines"][-1], "stderr-99")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_pilot_session_writes_log_files(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            day = tmp / "kabu_native" / "results" / "small_paper" / "20260701"
            before = day / "live_session_080000"
            before.mkdir(parents=True)
            after = day / "live_session_080616"
            state = make_state(
                tmp,
                tmp / "kabu_native",
                DailyRunnerOptions(day_stamp="20260701", dry_run_only=False),
            )
            state.am_prep = {
                "am_csv": "kabu_native/results/reports/universe_core10_dynamic40_am_20260701.csv"
            }

            class FakeProc:
                returncode = 1
                stdout = "session ok\n"
                stderr = "WARN: post-run cleanup failed\n"

            calls = {"n": 0}

            def fake_list(st) -> list[Path]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return [before]
                after.mkdir(parents=True, exist_ok=True)
                (after / "small_paper_summary.json").write_text(
                    json.dumps(
                        {
                            "am_pm_session": {"kind": "am"},
                            "stop_reason": "completed",
                            "push_messages": 100,
                            "gate_evaluations": 10,
                            "generated_at": "2026-07-01T09:00:00+09:00",
                            "ended_at": "2026-07-01T11:00:00+09:00",
                            "accepted_count": 43,
                        }
                    ),
                    encoding="utf-8",
                )
                return [before, after]

            with patch("runner.am_pm_daily_runner.subprocess.run", return_value=FakeProc()):
                with patch("runner.am_pm_daily_runner._list_session_dirs", side_effect=fake_list):
                    result = run_pilot_session(state, session="am")

            self.assertTrue((after / PILOT_STDOUT_LOG).is_file())
            self.assertTrue((after / PILOT_STDERR_LOG).is_file())
            self.assertIn("WARN", (after / PILOT_STDERR_LOG).read_text(encoding="utf-8"))
            self.assertEqual(result.get("pilot_verdict"), "completed_with_warnings")
            self.assertTrue(result.get("pilot_stdout_path"))
            self.assertTrue(result.get("pilot_stderr_path"))
            summary = json.loads((after / "small_paper_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary.get("pilot_exit_display"), "1 (warning)")
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_am_failed_records_first_exception(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            state = make_state(
                tmp,
                tmp / "kabu_native",
                DailyRunnerOptions(day_stamp="20260702"),
            )
            live = {
                "exit_code": 1,
                "first_exception": "RuntimeError: boom",
                "first_traceback": TRACEBACK_SAMPLE,
                "first_error_line": "RuntimeError: boom",
                "stderr_last_20_lines": tail_lines(TRACEBACK_SAMPLE),
                "stdout_last_20_lines": [],
                "pilot_stdout_path": "kabu_native/results/small_paper/20260702/live_session_x/pilot_stdout.log",
                "pilot_stderr_path": "kabu_native/results/small_paper/20260702/live_session_x/pilot_stderr.log",
            }
            _record_pilot_hard_failure(state, session="am", live=live)
            diag = state.sessions.get("am_pilot_failure") or {}
            self.assertEqual(diag.get("first_exception"), "RuntimeError: boom")
            self.assertIn("Traceback", diag.get("first_traceback", ""))
            payload = build_summary_payload(state)
            state.am_live = live
            payload = build_summary_payload(state)
            self.assertEqual(payload.get("am_first_exception"), "RuntimeError: boom")
            self.assertEqual(payload.get("am_pilot_exit_code"), 1)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_discord_pilot_exit_display(self) -> None:
        lines = format_runtime_health_lines(
            {"pilot_exit_code": 1, "pilot_subprocess_verdict": "completed_with_warnings"}
        )
        self.assertTrue(any(l == "Pilot Exit: 1 (warning)" for l in lines))

        lines_fail = format_runtime_health_lines(
            {"pilot_exit_code": 1, "pilot_subprocess_verdict": "failed"}
        )
        self.assertTrue(any(l == "Pilot Exit: 1 (failed)" for l in lines_fail))

        lines_ok = format_runtime_health_lines({"pilot_exit_display": "0"})
        self.assertTrue(any(l == "Pilot Exit: 0" for l in lines_ok))

    def test_apply_verdict_policy_uses_log_summaries(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp
            session = repo / "kabu_native" / "results" / "small_paper" / "20260701" / "live_session_080616"
            session.mkdir(parents=True)
            (session / "small_paper_summary.json").write_text(
                json.dumps(
                    {
                        "am_pm_session": {"kind": "am"},
                        "stop_reason": "completed",
                        "push_messages": 100,
                        "gate_evaluations": 10,
                        "generated_at": "2026-07-01T09:00:00+09:00",
                        "ended_at": "2026-07-01T11:00:00+09:00",
                        "accepted_count": 43,
                    }
                ),
                encoding="utf-8",
            )
            rel = "kabu_native/results/small_paper/20260701/live_session_080616"
            state = make_state(repo, repo / "kabu_native", DailyRunnerOptions(day_stamp="20260701"))
            live: dict = {
                "exit_code": 1,
                "session_dir": rel,
                "stderr_last_20_lines": ["warn-a", "warn-b"],
                "stdout_last_20_lines": ["out-a"],
            }
            _apply_pilot_verdict_policy(state, live)
            notes = live.get("warning_notes") or []
            self.assertTrue(any("stderr_summary:" in n for n in notes))
            self.assertTrue(any("stdout_summary:" in n for n in notes))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_persist_without_session_dir_still_tails(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            state = make_state(
                tmp,
                tmp / "kabu_native",
                DailyRunnerOptions(day_stamp="20260703"),
            )
            result: dict = {"exit_code": 1}
            _persist_pilot_subprocess_artifacts(
                state,
                result,
                session_dir=None,
                fallback_dirs=[],
                stdout="line-out\n",
                stderr=TRACEBACK_SAMPLE,
                proc_error=None,
                proc_exc_type=None,
            )
            self.assertIn("RuntimeError", result.get("first_exception", ""))
            self.assertEqual(result.get("stdout_last_20_lines"), ["line-out"])
            self.assertTrue(result.get("stderr_last_20_lines"))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

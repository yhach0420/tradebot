"""Phase642b: completed_with_warnings policy bug fix tests."""

from __future__ import annotations

import io
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
from runner.pilot_subprocess_logging import (  # noqa: E402
    is_post_session_subprocess_failure,
    session_stop_reason_soft_ok,
)

_UNICODE_TB = (
    "Traceback (most recent call last):\n"
    '  File "kabu_native/scripts/run_small_paper_pilot.py", line 363, in main\n'
    "    print(json.dumps(result.summary, ensure_ascii=False, indent=2))\n"
    "UnicodeEncodeError: 'cp932' codec can't encode character '\\u2014'\n"
)


def _write_session_end_summary(session_dir: Path, *, accepted: int = 12) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "small_paper_summary.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-06T09:06:32+09:00",
                "ended_at": "2026-07-06T11:25:00+09:00",
                "stop_reason": "session_end",
                "push_messages": 100000,
                "gate_evaluations": 5000,
                "accepted_count": accepted,
                "rejected_count": 4000,
                "runtime_sec": 7000.0,
            }
        ),
        encoding="utf-8",
    )


def _20260706_live(repo_rel: str) -> dict:
    return {
        "exit_code": 1,
        "session_dir": repo_rel,
        "summary_found": True,
        "first_exception": (
            "UnicodeEncodeError: 'cp932' codec can't encode character '\\u2014' "
            "in position 4376: illegal multibyte sequence"
        ),
        "first_traceback": _UNICODE_TB,
        "stderr_last_20_lines": _UNICODE_TB.splitlines(),
    }


class Phase642bFixTests(unittest.TestCase):
    def test_session_end_is_soft_ok_stop_reason(self) -> None:
        self.assertTrue(session_stop_reason_soft_ok("session_end"))
        self.assertTrue(session_stop_reason_soft_ok("completed"))
        self.assertFalse(session_stop_reason_soft_ok("keyboard_interrupt"))

    def test_20260706_like_session_end_unicode_print_is_soft_ok(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp
            rel = "kabu_native/results/small_paper/20260706/live_session_080937"
            session = repo / rel.replace("/", "\\")
            _write_session_end_summary(session)
            live = _20260706_live(rel)
            self.assertTrue(is_post_session_subprocess_failure(live))
            ok, details = _pilot_completed_with_warnings(repo, live)
            self.assertTrue(ok, details)
            self.assertEqual(details.get("stop_reason"), "session_end")
            self.assertFalse(_pilot_failed_hard(live, repo_root=repo))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_verdict_completed_with_warnings_for_20260706(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp
            rel = "kabu_native/results/small_paper/20260706/live_session_080937"
            _write_session_end_summary(repo / rel.replace("/", "\\"))
            state = make_state(repo, repo / "kabu_native", DailyRunnerOptions(day_stamp="20260706"))
            live = _20260706_live(rel)
            _apply_pilot_verdict_policy(state, live)
            self.assertEqual(live["pilot_verdict"], "completed_with_warnings")
            self.assertTrue(live["pilot_ok"])
            self.assertTrue(
                any("post_session_summary_print_failed" in n for n in live.get("warning_notes") or [])
            )
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_am_20260706_continues_to_pm(self) -> None:
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            repo = tmp
            rel = "kabu_native/results/small_paper/20260706/live_session_080937"
            _write_session_end_summary(repo / rel.replace("/", "\\"))
            am_live = _20260706_live(rel)
            _apply_pilot_verdict_policy(
                make_state(repo, repo / "kabu_native", DailyRunnerOptions(day_stamp="20260706")),
                am_live,
            )
            state = make_state(
                repo,
                repo / "kabu_native",
                DailyRunnerOptions(day_stamp="20260706", skip_safety=True, skip_kabu=True),
            )
            with patch("runner.am_pm_daily_runner.preflight", return_value=True):
                with patch(
                    "runner.am_pm_daily_runner.build_am_universe",
                    return_value={"ok": True, "am_csv": "kabu_native/results/reports/u.csv"},
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
                                with patch("runner.am_pm_daily_runner.build_pm_universe") as pm:
                                    pm.return_value = {"ok": False, "error": "stop"}
                                    rc = _run_daily_runner_body(state)
            self.assertNotEqual(state.verdict, "am_failed")
            pm.assert_called_once()
            self.assertEqual(rc, 2)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_safe_print_emits_stderr_on_unicode_error(self) -> None:
        import importlib.util

        pilot_py = NATIVE / "scripts" / "run_small_paper_pilot.py"
        spec = importlib.util.spec_from_file_location("run_small_paper_pilot", pilot_py)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        class _Result:
            output_dir = Path("/tmp/out")
            summary = {"comparison_note": "em\u2014dash"}

        buf = io.StringIO()
        with patch("sys.stdout", io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")):
            with patch("sys.stderr", buf):
                warn = mod._emit_pilot_result_summary(_Result())
        self.assertIsNotNone(warn)
        self.assertIn("pilot_summary_print_failed", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

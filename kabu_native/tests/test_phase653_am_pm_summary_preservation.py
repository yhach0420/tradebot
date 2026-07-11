"""Phase653 AM/PM summary preservation tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from runner.am_pm_daily_runner import (  # noqa: E402
    DailyRunnerOptions,
    build_summary_payload,
    make_state,
    write_outputs,
)
from small_paper.am_pm_summary_preservation import (  # noqa: E402
    SESSION_SUMMARY_AM,
    SESSION_SUMMARY_PM,
    preserve_daily_runner_summaries,
    preserve_session_summary_copy,
    session_kind_from_summary,
)


class Phase653SummaryPreservationTests(unittest.TestCase):
    def test_session_kind_from_summary(self) -> None:
        self.assertEqual(session_kind_from_summary({"am_pm_session": {"kind": "pm"}}), "pm")

    def test_preserve_am_copy_keeps_original(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "small_paper_summary.json"
            payload = {"am_pm_session": {"kind": "am"}, "accepted_count": 3}
            src.write_text(json.dumps(payload), encoding="utf-8")
            dest = preserve_session_summary_copy(root, session_kind="am")
            self.assertIsNotNone(dest)
            assert dest is not None
            self.assertEqual(dest.name, SESSION_SUMMARY_AM)
            self.assertTrue(src.is_file())
            copied = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(copied["accepted_count"], 3)

    def test_preserve_pm_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "small_paper_summary.json").write_text(
                json.dumps({"am_pm_session": {"kind": "pm"}, "accepted_count": 5}),
                encoding="utf-8",
            )
            dest = preserve_session_summary_copy(root, session_kind="pm")
            self.assertIsNotNone(dest)
            assert dest is not None
            self.assertEqual(dest.name, SESSION_SUMMARY_PM)

    def test_daily_runner_mirror_prefers_am_pm_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            am_sess = repo / "kabu_native/results/small_paper/20260706/live_session_080937"
            pm_sess = repo / "kabu_native/results/small_paper/20260706/live_session_125500"
            am_sess.mkdir(parents=True)
            pm_sess.mkdir(parents=True)
            (am_sess / "small_paper_summary.json").write_text(
                json.dumps({"am_pm_session": {"kind": "am"}, "tag": "latest"}),
                encoding="utf-8",
            )
            (am_sess / SESSION_SUMMARY_AM).write_text(
                json.dumps({"am_pm_session": {"kind": "am"}, "tag": "preserved"}),
                encoding="utf-8",
            )
            (pm_sess / "small_paper_summary.json").write_text(
                json.dumps({"am_pm_session": {"kind": "pm"}, "tag": "pm"}),
                encoding="utf-8",
            )
            paths = preserve_daily_runner_summaries(
                repo,
                day_stamp="20260706",
                am_session_dir=am_sess,
                pm_session_dir=pm_sess,
            )
            self.assertIsNotNone(paths["am_summary_path"])
            self.assertIsNotNone(paths["pm_summary_path"])
            am_daily = json.loads(Path(paths["am_summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(am_daily["tag"], "preserved")

    def test_write_outputs_adds_summary_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            native = repo / "kabu_native"
            reports = native / "results" / "reports"
            reports.mkdir(parents=True)
            am_sess = native / "results" / "small_paper" / "20260706" / "live_session_080937"
            pm_sess = native / "results" / "small_paper" / "20260706" / "live_session_125500"
            am_sess.mkdir(parents=True)
            pm_sess.mkdir(parents=True)
            (am_sess / "small_paper_summary.json").write_text(
                json.dumps({"am_pm_session": {"kind": "am"}}),
                encoding="utf-8",
            )
            (pm_sess / "small_paper_summary.json").write_text(
                json.dumps({"am_pm_session": {"kind": "pm"}}),
                encoding="utf-8",
            )
            state = make_state(repo, native, DailyRunnerOptions(day_stamp="20260706"))
            state.generated_at = "2026-07-07T00:00:00"
            state.verdict = "am_pm_daily_runner_ready"
            state.am_live = {"session_dir": "kabu_native/results/small_paper/20260706/live_session_080937"}
            state.pm_live = {"session_dir": "kabu_native/results/small_paper/20260706/live_session_125500"}
            write_outputs(state)
            payload = build_summary_payload(state)
            self.assertTrue(payload.get("am_summary_path"))
            self.assertTrue(payload.get("pm_summary_path"))


if __name__ == "__main__":
    unittest.main()

"""Phase399 historical position-CAP backfill tests."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase399_historical_position_cap_backfill import (  # noqa: E402
    CAP,
    FIXTURE_CAPITAL_SHADOW_ACCEPTED,
    FIXTURE_CAPITAL_SHADOW_PNL,
    FIXTURE_POSITION_CAP_ACCEPTED,
    POSITION_CAP_REJECT,
    classify_session,
    compare_serial_parallel,
    discover_sessions,
    process_session,
    reduce_session_results,
    run_phase399_backfill,
)


def _session_file_hash(session_dir: Path) -> str:
    h = hashlib.sha256()
    for rel in (
        "small_paper_summary.json",
        "small_paper_events.csv",
        "structural_trades.csv",
        "structural_observer_review.json",
    ):
        path = session_dir / rel
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


class TestPhase399HistoricalBackfill(unittest.TestCase):
    def test_discover_sessions_in_range(self) -> None:
        sp = REPO / "results" / "small_paper"
        if not sp.is_dir():
            self.skipTest("small_paper missing")
        sessions = discover_sessions(small_paper_root=sp, start_day="20260529", end_day="20260615")
        self.assertTrue(sessions)
        self.assertTrue(all("live_session" in s.name for s in sessions))

    def test_classify_skips_debug_and_push_replay(self) -> None:
        self.assertEqual(classify_session(Path("/tmp/live_session_debug_x")), "skipped_debug")
        sp = REPO / "results" / "small_paper"
        replay_dirs = list(sp.glob("20260529/push_replay_*"))
        if replay_dirs:
            self.assertEqual(classify_session(replay_dirs[0]), "skipped_push_replay")

    def test_position_cap_reject_reason(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        row = process_session(session_dir, repo_root=REPO)
        rejected = [r for r in row["trade_rows"] if not r["position_cap_accepted"]]
        self.assertTrue(rejected)
        self.assertTrue(all(r["position_cap_reject_reason"] for r in rejected if r["position_cap_reject_reason"]))

    def test_position_cap_max_open_le_cap(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        row = process_session(session_dir, repo_root=REPO)
        self.assertLessEqual(int(row["position_cap_max_open"]), CAP)
        self.assertLessEqual(int(row["capital_shadow_max_open"]), CAP)

    def test_worker_failure_recorded(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        with patch(
            "research.phase399_historical_position_cap_backfill._position_cap_backfill",
            side_effect=RuntimeError("boom"),
        ):
            row = process_session(session_dir, repo_root=REPO)
        self.assertEqual(row["status"], "failed")
        reduced = reduce_session_results([row], start_day="20260615", end_day="20260615")
        self.assertEqual(reduced["counters"]["failed_sessions"], 1)

    def test_fixture_20260615_pm(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        row = process_session(session_dir, repo_root=REPO)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(int(row["position_cap_trade_count"]), FIXTURE_POSITION_CAP_ACCEPTED)
        self.assertEqual(int(row["capital_shadow_trade_count"]), FIXTURE_CAPITAL_SHADOW_ACCEPTED)
        self.assertEqual(float(row["capital_shadow_pnl_yen_100"]), FIXTURE_CAPITAL_SHADOW_PNL)

    def test_serial_parallel_match(self) -> None:
        fixture = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not fixture.is_dir():
            self.skipTest("fixture session missing")
        cmp = compare_serial_parallel(
            repo_root=REPO,
            start_day="20260615",
            end_day="20260615",
            max_workers=4,
        )
        self.assertTrue(cmp["match"])

    def test_parent_only_writes_reports(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        before = _session_file_hash(session_dir)
        out = REPO / "results" / "reports" / "_phase399_test_parent_write"
        run_phase399_backfill(
            repo_root=REPO,
            start_day="20260615",
            end_day="20260615",
            output_dir=out,
            parallel=False,
            max_workers=1,
        )
        self.assertEqual(before, _session_file_hash(session_dir))
        self.assertTrue((out / "phase399_historical_position_cap_backfill_summary.json").is_file())

    def test_reduce_verdict_historical_backfill_ready(self) -> None:
        session_dir = REPO / "results" / "small_paper" / "20260615" / "live_session_122531"
        if not session_dir.is_dir():
            self.skipTest("fixture session missing")
        row = process_session(session_dir, repo_root=REPO)
        reduced = reduce_session_results([row], start_day="20260615", end_day="20260615")
        self.assertEqual(reduced["verdict"], "historical_backfill_ready")
        self.assertTrue(reduced["validation"]["fixture_pass"])


if __name__ == "__main__":
    unittest.main()

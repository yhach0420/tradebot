"""Phase265 structural trades backfill tests."""

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

from research.structural_trades_backfill import (  # noqa: E402
    PERIOD_END,
    PERIOD_START,
    classify_session,
    enumerate_sessions,
    run_structural_trades_backfill,
)


class TestStructuralTradesBackfill(unittest.TestCase):
    def test_enumerate_sessions_in_period(self) -> None:
        sp = REPO / "kabu_native" / "results" / "small_paper"
        if not sp.is_dir():
            self.skipTest("small_paper root missing")
        sessions = enumerate_sessions(small_paper_root=sp)
        self.assertTrue(all(PERIOD_START <= s.parent.name <= PERIOD_END for s in sessions))

    def test_classify_skips_push_replay(self) -> None:
        sp = REPO / "kabu_native" / "results" / "small_paper"
        replay_dirs = list(sp.glob("20260529/push_replay_*"))
        if not replay_dirs:
            self.skipTest("push replay session missing")
        self.assertEqual(classify_session(replay_dirs[0]), "skipped_push_replay")

    def test_classify_skips_existing_structural(self) -> None:
        sp = REPO / "kabu_native" / "results" / "small_paper"
        existing = list(sp.glob("20260525/*/structural_trades.csv"))
        if not existing:
            self.skipTest("existing structural session missing")
        session_dir = existing[0].parent
        self.assertEqual(classify_session(session_dir), "skipped_out_of_period")

    def test_run_backfill_dry_counters(self) -> None:
        with patch(
            "research.structural_trades_backfill.backfill_session",
            return_value={
                "day": "20260529",
                "session": "live_session_test",
                "session_dir": "/tmp/x",
                "status": "generated",
                "source": "live",
                "rows_generated": 10,
                "structural_trade_count": 10,
                "structural_pf": 1.2,
                "error": "",
            },
        ), patch(
            "research.structural_trades_backfill.enumerate_sessions",
            return_value=[Path("/tmp/x")],
        ), patch(
            "research.structural_trades_backfill.classify_session",
            return_value="pending",
        ):
            result = run_structural_trades_backfill(repo_root=REPO)
        summary = result["summary"]
        self.assertEqual(summary["processed_session_count"], 1)
        self.assertEqual(summary["generated_structural_trades_count"], 1)
        self.assertEqual(summary["rows_generated_total"], 10)


if __name__ == "__main__":
    unittest.main()

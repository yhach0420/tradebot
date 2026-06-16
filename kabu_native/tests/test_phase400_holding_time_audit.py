"""Phase400 holding time audit tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase400_holding_time_audit import (  # noqa: E402
    enrich_trade,
    hold_seconds,
    normalize_exit_reason,
    run_holding_time_audit,
)


class TestPhase400HoldingTimeAudit(unittest.TestCase):
    def test_normalize_exit_reason(self) -> None:
        self.assertEqual(normalize_exit_reason("trailing_mfe_exit"), "trailing_mfe")
        self.assertEqual(normalize_exit_reason("overlap_replaced_review"), "overlap_replaced")
        self.assertEqual(normalize_exit_reason("session_end"), "session_close")
        self.assertEqual(normalize_exit_reason("stop_hit"), "stop_hit")

    def test_hold_seconds(self) -> None:
        sec = hold_seconds("2026-06-15T14:00:00+09:00", "2026-06-15T15:23:00+09:00")
        self.assertEqual(sec, 4980.0)

    def test_enrich_trade_winner(self) -> None:
        row = enrich_trade(
            {
                "entry_time": "2026-06-15T12:00:00+09:00",
                "exit_time": "2026-06-15T12:05:00+09:00",
                "exit_reason": "trailing_mfe_exit",
                "pnl_yen_100": "100",
                "position_cap_accepted": "True",
            }
        )
        self.assertEqual(row["hold_sec"], 300.0)
        self.assertTrue(row["is_winner"])
        self.assertEqual(row["exit_reason_bucket"], "trailing_mfe")

    def test_run_on_fixture_csv(self) -> None:
        src = REPO / "results" / "reports" / "phase399_historical_position_cap_backfill_trades.csv"
        if not src.is_file():
            self.skipTest("phase399 trades missing")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = run_holding_time_audit(
                repo_root=REPO,
                trades_path=src,
                output_dir=out,
                period_start="20260615",
                period_end="20260615",
            )
            self.assertTrue((out / "phase400_holding_time_summary.json").is_file())
            summary = result["summary"]
            self.assertGreater(summary["position_cap_accepted_trade_count"], 0)
            hs = summary["hold_duration_sec"]
            self.assertGreater(hs["max_hold_sec"], hs["median_hold_sec"])


if __name__ == "__main__":
    unittest.main()

"""Phase410 duplicate re-entry audit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase410_duplicate_reentry_audit import (  # noqa: E402
    AUDIT_DAY,
    apply_counterfactual_policy,
    build_overlap_replace_events,
    load_session_trades,
    normalize_structural_row,
    run_phase410_audit,
)


class TestPhase410DuplicateReentry(unittest.TestCase):
    def test_normalize_maps_close_time(self) -> None:
        row = normalize_structural_row(
            {
                "symbol": "9984.T",
                "entry_time": "2026-06-16T09:00:00+09:00",
                "close_time": "2026-06-16T09:05:00+09:00",
                "entry_price": 1000.0,
                "close_price": 1010.0,
                "close_reason": "overlap_replaced_review",
            },
            day=AUDIT_DAY,
            session="test",
        )
        self.assertEqual(row["exit_time"], "2026-06-16T09:05:00+09:00")

    def test_overlap_events_same_symbol(self) -> None:
        trades = [
            normalize_structural_row(
                {
                    "symbol": "6981.T",
                    "entry_time": "2026-06-16T12:50:07+09:00",
                    "close_time": "2026-06-16T12:50:27+09:00",
                    "entry_price": 100.0,
                    "close_price": 100.0,
                    "close_reason": "overlap_replaced_review",
                },
                day=AUDIT_DAY,
                session="pm",
            ),
            normalize_structural_row(
                {
                    "symbol": "6981.T",
                    "entry_time": "2026-06-16T12:50:27+09:00",
                    "close_time": "2026-06-16T12:50:32+09:00",
                    "entry_price": 100.0,
                    "close_price": 100.0,
                    "close_reason": "overlap_replaced_review",
                },
                day=AUDIT_DAY,
                session="pm",
            ),
        ]
        events = build_overlap_replace_events(trades, session="pm")
        self.assertTrue(events)
        self.assertTrue(all(e.get("same_symbol") for e in events))

    def test_counterfactual_reduces_trades(self) -> None:
        trades = [
            normalize_structural_row(
                {
                    "symbol": "6981.T",
                    "entry_time": "2026-06-16T12:50:07+09:00",
                    "close_time": "2026-06-16T12:50:27+09:00",
                    "entry_price": 100.0,
                    "close_price": 100.0,
                    "close_reason": "overlap_replaced_review",
                },
                day=AUDIT_DAY,
                session="pm",
            ),
            normalize_structural_row(
                {
                    "symbol": "6981.T",
                    "entry_time": "2026-06-16T12:50:27+09:00",
                    "close_time": "2026-06-16T12:50:32+09:00",
                    "entry_price": 100.0,
                    "close_price": 101.0,
                    "close_reason": "trailing_mfe_exit",
                },
                day=AUDIT_DAY,
                session="pm",
            ),
        ]
        kept = apply_counterfactual_policy(trades, policy="same_symbol_open_reentry_reject")
        self.assertLess(len(kept), len(trades))

    def test_run_audit_on_20260616(self) -> None:
        am_dir = REPO / "results" / "small_paper" / AUDIT_DAY / "live_session_081407"
        if not (am_dir / "structural_trades.csv").is_file():
            self.skipTest("20260616 session data missing")
        result = run_phase410_audit(repo_root=REPO, output_dir=REPO / "results" / "reports")
        self.assertEqual(result["summary"]["verdict"], "PASS")
        ma = result["summary"]["mandatory_answers"]
        self.assertTrue(ma.get("2_overlap_replaced_is_same_symbol_replace"))
        self.assertTrue(ma.get("4_day_count_zero_is_bug"))


if __name__ == "__main__":
    unittest.main()

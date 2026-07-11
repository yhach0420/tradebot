"""Phase675 — Recent early STOP focus tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase675_recent_early_stop_focus import (  # noqa: E402
    _early_stop_cases_709,
    _missed_cases,
    _rule_flags,
    run_audit,
)


class TestPhase675Helpers(unittest.TestCase):
    def test_rule_flags_none_without_microsequence(self) -> None:
        flags = _rule_flags({"microsequence_ok": False, "early_stop": True})
        self.assertIsNone(flags["A"])

    def test_missed_cases_filter(self) -> None:
        cases = [
            {"symbol": "A", "missed_by_A_and_C": True},
            {"symbol": "B", "missed_by_A_and_C": False},
        ]
        self.assertEqual(len(_missed_cases(cases)), 1)

    def test_early_stop_case_builder(self) -> None:
        trades = [
            {
                "day": "2026-07-09",
                "session": "live_session_082103",
                "symbol": "1111.T",
                "entry_time": "2026-07-09T09:10:00+09:00",
                "early_stop": True,
                "microsequence_ok": True,
                "bounce_from_recent_low": 0.3,
                "fall_from_recent_high": -0.2,
                "hold_sec": 60,
                "pnl_yen_100": -1000,
                "exit_reason": "stop_hit",
            }
        ]
        rows = _early_stop_cases_709(trades)
        self.assertEqual(len(rows), 1)
        self.assertIn("captured_by_A", rows[0])


def test_phase675_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {
        "FOUND_RECENT_SIGNAL",
        "FOUND_CAP_ENTRY_PRESSURE",
        "HOLD",
        "REJECT",
    }
    out = root / "results" / "reports" / "phase675_recent_early_stop_focus"
    assert (out / "phase675_recent_focus_report.json").is_file()


if __name__ == "__main__":
    unittest.main()

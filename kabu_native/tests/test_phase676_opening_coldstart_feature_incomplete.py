"""Phase676 — Opening cold-start audit tests."""

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

from research.phase676_opening_coldstart_feature_incomplete import (  # noqa: E402
    _classify_gap_reason,
    _is_shallow_fall,
    _missed_by_a_and_c,
    run_audit,
)


class TestPhase676Helpers(unittest.TestCase):
    def test_gap_reason_ok(self) -> None:
        self.assertEqual(_classify_gap_reason({"microsequence_ok": True}), "ok")

    def test_shallow_fall(self) -> None:
        self.assertTrue(_is_shallow_fall({"fall_from_recent_high": 0.0}))
        self.assertFalse(_is_shallow_fall({"fall_from_recent_high": -0.5}))

    def test_missed_by_ac_includes_gap(self) -> None:
        self.assertTrue(_missed_by_a_and_c({"microsequence_ok": False}))


def test_phase676_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {
        "FOUND_DATA_COMPLETENESS_SIGNAL",
        "FOUND_SHALLOW_BOUNCE_SIGNAL",
        "FOUND_OPENING_COLDSTART_SIGNAL",
        "HOLD",
        "REJECT",
    }
    out = root / "results" / "reports" / "phase676_opening_coldstart_feature_incomplete"
    assert (out / "phase676_report.json").is_file()


if __name__ == "__main__":
    unittest.main()

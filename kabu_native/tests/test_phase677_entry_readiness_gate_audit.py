"""Phase677 — Entry readiness gate audit tests."""

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

from research.phase677_entry_readiness_gate_audit import (  # noqa: E402
    CODE_PATH_SUMMARY,
    _missing_categories,
    _readiness_predicates,
    run_audit,
)


class TestPhase677Helpers(unittest.TestCase):
    def test_code_path_documents_no_lfc_gate(self) -> None:
        self.assertIn("NONE", CODE_PATH_SUMMARY["live_feature_complete_gate"])

    def test_readiness_predicates_count(self) -> None:
        self.assertGreaterEqual(len(_readiness_predicates()), 15)

    def test_missing_categories_price_gap(self) -> None:
        cats = _missing_categories(
            {
                "microsequence_ok": False,
                "price_history_source": "none",
                "pre_price_points_120s": 0,
                "entry_time": "2026-07-09T09:07:56+09:00",
            },
            {},
        )
        self.assertIn("price_history_insufficient", cats)


def test_phase677_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {
        "FOUND_READINESS_BUG",
        "FOUND_MINIMAL_READINESS_GATE",
        "HOLD",
        "REJECT",
    }
    out = root / "results" / "reports" / "phase677_entry_readiness_gate_audit"
    assert (out / "phase677_report.json").is_file()


if __name__ == "__main__":
    unittest.main()

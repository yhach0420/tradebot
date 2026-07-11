"""Phase678 — Readiness gate robustness tests."""

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

from research.phase678_readiness_gate_robustness import (  # noqa: E402
    _candidate_predicates,
    _is_winner,
    run_audit,
)


class TestPhase678Helpers(unittest.TestCase):
    def test_primary_candidates_exist(self) -> None:
        ids = [c[0] for c in _candidate_predicates()]
        self.assertIn("I_precision", ids)
        self.assertIn("H_economics", ids)

    def test_winner_helper(self) -> None:
        self.assertTrue(_is_winner({"pnl_yen_100": 100}))


def test_phase678_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {"READINESS_SHADOW_CANDIDATE", "HOLD", "REJECT"}
    out = root / "results" / "reports" / "phase678_readiness_gate_robustness"
    assert (out / "phase678_report.json").is_file()


if __name__ == "__main__":
    unittest.main()

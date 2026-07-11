"""Phase682 — Shadow portfolio consistency audit tests."""

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

from research.phase682_shadow_portfolio_consistency import (  # noqa: E402
    PHASE679_H_REFERENCE,
    PHASE681_H_REFERENCE,
    run_audit,
)


class TestPhase682Helpers(unittest.TestCase):
    def test_reference_values_present(self) -> None:
        self.assertEqual(PHASE679_H_REFERENCE["blocked_count"], 115)
        self.assertEqual(PHASE681_H_REFERENCE["blocked_count"], 82)


def test_phase682_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {
        "SHADOW_METRICS_CONSISTENT",
        "H_METRIC_DRIFT_EXPLAINED",
        "SHADOW_METRIC_BUG_FOUND",
        "HOLD",
    }
    out = root / "results" / "reports" / "phase682_shadow_portfolio_consistency"
    for name in (
        "phase682_report.json",
        "phase682_h_diff_trade_ids.csv",
        "phase682_h_metric_reconciliation.csv",
        "phase682_pool_comparison.csv",
        "phase682_shadow_definition_audit.md",
        "phase682_decision.md",
    ):
        assert (out / name).is_file(), name


if __name__ == "__main__":
    unittest.main()

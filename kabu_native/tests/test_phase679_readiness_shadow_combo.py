"""Phase679 — Readiness shadow combo study tests."""

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

from research.phase679_readiness_shadow_combo import (  # noqa: E402
    _combo_scenarios,
    _flags,
    _pred_c,
    _pred_h,
    _pred_i,
    run_audit,
)


class TestPhase679Helpers(unittest.TestCase):
    def test_combo_scenario_count(self) -> None:
        self.assertEqual(len(_combo_scenarios()), 14)

    def test_i_predicate(self) -> None:
        self.assertTrue(_pred_i({"live_feature_complete": False, "entry_expectancy_score_v2": 2.0}))
        self.assertFalse(_pred_i({"live_feature_complete": True, "entry_expectancy_score_v2": 2.0}))

    def test_flags_tuple(self) -> None:
        i, h, c = _flags(
            {
                "live_feature_complete": False,
                "entry_expectancy_score_v2": 2.0,
                "bounce_from_recent_low": 0.5,
                "microsequence_ok": False,
            }
        )
        self.assertTrue(i)
        self.assertTrue(h)
        self.assertFalse(c)


def test_phase679_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {"READINESS_SHADOW_CANDIDATE", "HOLD", "REJECT"}
    out = root / "results" / "reports" / "phase679_readiness_shadow_combo"
    for name in (
        "phase679_shadow_report.json",
        "phase679_shadow_trades.csv",
        "phase679_daily_forward_summary.csv",
        "phase679_combo_counterfactual.csv",
        "phase679_combo_overlap.csv",
        "phase679_microsequence_c_combo_report.json",
        "phase679_decision.md",
    ):
        assert (out / name).is_file(), name


if __name__ == "__main__":
    unittest.main()

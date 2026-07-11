"""Phase679B — H economics winner quality audit tests."""

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

from research.phase679b_h_economics_winner_quality import (  # noqa: E402
    _classify_h_blocked_winner,
    _pnl_decomposition,
    _pred_h,
    _pred_i,
    run_audit,
)


class TestPhase679BHelpers(unittest.TestCase):
    def test_pnl_decomposition(self) -> None:
        blocked = [
            {"pnl_yen_100": -1000},
            {"pnl_yen_100": 2000},
        ]
        d = _pnl_decomposition(blocked)
        self.assertEqual(d["avoided_loss_yen"], 1000.0)
        self.assertEqual(d["lost_profit_yen"], 2000.0)
        self.assertEqual(d["net_delta_yen"], -1000.0)

    def test_intended_winner_class(self) -> None:
        cls = _classify_h_blocked_winner(
            {
                "pnl_yen_100": 12000,
                "live_feature_complete": False,
                "bounce_from_recent_low": 0.5,
                "entry_expectancy_score_v2": 2.0,
            }
        )
        self.assertEqual(cls, "A_intended_winner")

    def test_predicates(self) -> None:
        t = {"live_feature_complete": False, "bounce_from_recent_low": 0.5, "entry_expectancy_score_v2": 2.0}
        self.assertTrue(_pred_h(t))
        self.assertTrue(_pred_i(t))


def test_phase679b_audit_smoke():
    root = Path(__file__).resolve().parents[1]
    if not (root / "results" / "small_paper" / "20260709").is_dir():
        pytest.skip("7/9 paper missing")
    report = run_audit()
    assert report["verdict"] in {
        "H_STRONG_CANDIDATE",
        "H_NEEDS_REFINEMENT",
        "I_OR_H_CANDIDATE",
        "I_ONLY_CONTINUE",
        "HOLD",
        "REJECT",
    }
    out = root / "results" / "reports" / "phase679b_h_economics_winner_quality"
    for name in (
        "phase679b_report.json",
        "phase679b_h_blocked_winner_quality.csv",
        "phase679b_h_big_winner_audit.csv",
        "phase679b_h_loser_quality.csv",
        "phase679b_i_h_decomposition.csv",
        "phase679b_microsequence_combo_quality.csv",
        "phase679b_refined_h_sweep.csv",
        "phase679b_decision.md",
    ):
        assert (out / name).is_file(), name


if __name__ == "__main__":
    unittest.main()

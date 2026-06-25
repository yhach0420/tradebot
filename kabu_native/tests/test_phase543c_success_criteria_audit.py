"""Phase543C success criteria audit unit tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase543c_success_criteria_audit import (  # noqa: E402
    CRITERIA_DEFS,
    PHASE543C_VERDICT,
    _evaluate_checks,
    _weighted_score,
)


class TestPhase543CSuccessCriteriaAudit(unittest.TestCase):
    def test_verdict(self) -> None:
        self.assertEqual(PHASE543C_VERDICT, "phase543c_success_criteria_audit_done")

    def test_twelve_criteria(self) -> None:
        self.assertEqual(len(CRITERIA_DEFS), 12)

    def test_weighted_score_max_100(self) -> None:
        self.assertEqual(sum(int(c.get("weighted_points") or 0) for c in CRITERIA_DEFS), 100)

    def test_evaluate_checks(self) -> None:
        ctx = {
            "baseline_pnl": -100.0,
            "baseline_pf": 1.0,
            "baseline_maxdd": 1000.0,
            "baseline_mfe0": 100,
            "baseline_np": 50,
            "guard_only_lost_big": {"G_B": 100},
        }
        s = {
            "guard_id": "G_B",
            "total_pnl_yen_100": 50.0,
            "profit_factor": 1.2,
            "max_drawdown_yen_100": 500.0,
            "mfe0_count": 50,
            "no_progress_count": 30,
            "trade_retention_rate": 0.35,
            "lost_big_winner_count": 70,
            "recovered_big_winner_count": 5,
            "reintroduced_mfe0_count": 5,
            "improvement_day_rate": 0.65,
        }
        ch = _evaluate_checks(s, ctx=ctx, orig_dep={"top3_symbol_exclusion_net_yen_100": 0}, dep={"top3_symbol_exclusion_net_yen_100": 10})
        self.assertTrue(ch["pnl_gt_baseline"])
        ws = _weighted_score(s, ch)
        self.assertGreater(ws["weighted_score"], 0)


if __name__ == "__main__":
    unittest.main()

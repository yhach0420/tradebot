"""Phase426 boundary hold distribution tests."""

from __future__ import annotations

import unittest
from pathlib import Path

TRADEBOT = Path(__file__).resolve().parents[1].parent


class Phase426BoundaryHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (TRADEBOT / "kabu_native" / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_phase426_audit_runs(self) -> None:
        from research.phase426_boundary_hold_distribution_audit import run_phase426_audit

        result = run_phase426_audit(repo_root=TRADEBOT)
        summary = result.get("summary") or {}
        baseline = summary.get("baseline") or {}
        self.assertEqual(int(baseline.get("accepted_count") or 0), 678)
        self.assertEqual(int(baseline.get("boundary_eligible_count") or 0), 373)
        self.assertEqual(int(baseline.get("boundary_hit_count_phase423_reported") or 0), 0)
        self.assertGreater(int(baseline.get("boundary_hit_count_raw_sim") or 0), 300)
        self.assertIn(summary.get("verdict"), (
            "boundary_not_reaching_time",
            "boundary_conditions_too_strict",
            "boundary_low_value",
        ))
        holds = (summary.get("mandatory_answers") or {}).get("1_hold_counts") or {}
        self.assertGreaterEqual(int(holds.get("5m") or 0), int(baseline.get("boundary_eligible_count") or 0))


if __name__ == "__main__":
    unittest.main()

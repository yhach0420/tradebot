"""Phase427 no progress true attribution tests."""

from __future__ import annotations

import unittest
from pathlib import Path

TRADEBOT = Path(__file__).resolve().parents[1].parent


class Phase427NoProgressAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (TRADEBOT / "kabu_native" / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_phase427_audit_runs(self) -> None:
        from research.phase427_no_progress_true_attribution_audit import run_phase427_audit

        result = run_phase427_audit(repo_root=TRADEBOT)
        summary = result.get("summary") or {}
        self.assertEqual(int(summary.get("accepted_count_input") or 0), 678)
        self.assertEqual(int(summary.get("evaluated_trade_count") or 0), 678)
        self.assertIn(
            summary.get("verdict"),
            ("adopt_candidate", "shadow_continue", "reject"),
        )
        integrity = summary.get("integrity_audit") or {}
        self.assertEqual(integrity.get("post_baseline_violations"), 0)
        reach = summary.get("reach_86_subset") or {}
        self.assertEqual(int(reach.get("reached_count") or 0), 86)


if __name__ == "__main__":
    unittest.main()

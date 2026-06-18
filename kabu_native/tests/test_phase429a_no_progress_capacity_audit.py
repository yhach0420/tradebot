"""Phase429A capacity audit tests."""

from __future__ import annotations

import unittest
from pathlib import Path

TRADEBOT = Path(__file__).resolve().parents[1].parent


class Phase429ACapacityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (TRADEBOT / "kabu_native" / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_part_a_is_exit_only(self) -> None:
        from research.phase429a_no_progress_capacity_audit import run_phase429a_audit

        result = run_phase429a_audit(repo_root=TRADEBOT)
        part_a = (result.get("summary") or {}).get("part_a_phase427_428_nature") or {}
        self.assertEqual(part_a.get("replay_type"), "exit_only")
        self.assertFalse(part_a.get("includes_capacity_reuse"))

    def test_capacity_replay_runs(self) -> None:
        from research.phase429a_no_progress_capacity_audit import run_phase429a_audit

        result = run_phase429a_audit(repo_root=TRADEBOT)
        summary = result.get("summary") or {}
        cmp_ = summary.get("comparison") or {}
        self.assertIn("A_baseline", cmp_)
        self.assertIn("C_capacity_aware", cmp_)
        self.assertEqual(int(summary.get("integrity", {}).get("post_baseline_violations") or 0), 0)


if __name__ == "__main__":
    unittest.main()

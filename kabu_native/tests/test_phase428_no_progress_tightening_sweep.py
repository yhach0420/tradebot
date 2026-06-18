"""Phase428 tightening sweep tests."""

from __future__ import annotations

import unittest
from pathlib import Path

TRADEBOT = Path(__file__).resolve().parents[1].parent


class Phase428TighteningSweepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (TRADEBOT / "kabu_native" / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_policy_grid_includes_fixed(self) -> None:
        from research.phase428_no_progress_tightening_sweep import (
            PHASE427_FIXED_KEY,
            iter_all_policies,
        )

        keys = {p.policy_key for p in iter_all_policies()}
        self.assertIn(PHASE427_FIXED_KEY, keys)
        self.assertGreater(len(keys), 100)

    def test_phase428_sweep_runs(self) -> None:
        from research.phase428_no_progress_tightening_sweep import run_phase428_sweep

        result = run_phase428_sweep(repo_root=TRADEBOT)
        summary = result.get("summary") or {}
        self.assertGreater(int(summary.get("policy_count") or 0), 100)
        self.assertEqual(int(summary.get("evaluated_trade_count") or 0), 678)
        self.assertIn(
            summary.get("verdict"),
            (
                "adopt_candidate_found",
                "fixed_policy_best",
                "no_policy_better",
                "insufficient_price_path",
            ),
        )
        grid = result.get("_grid_rows") or []
        fixed = next(r for r in grid if r["policy_key"] == "fixed_900_mfe0.8_pnl0.2")
        self.assertAlmostEqual(
            float(fixed.get("delta_pnl_vs_baseline") or 0),
            81920.69,
            delta=500.0,
        )


if __name__ == "__main__":
    unittest.main()

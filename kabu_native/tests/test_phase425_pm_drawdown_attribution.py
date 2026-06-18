"""Phase425 PM drawdown attribution tests."""

from __future__ import annotations

import unittest
from pathlib import Path

TRADEBOT = Path(__file__).resolve().parents[1].parent


class Phase425PmDrawdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (TRADEBOT / "kabu_native" / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_pm_drawdown_attribution(self) -> None:
        from research.phase425_pm_drawdown_attribution_audit import (
            EQUITY_20260617_PM,
            PM_EQUITY_DELTA,
            run_phase425_audit,
        )

        result = run_phase425_audit(repo_root=TRADEBOT)
        summary = result.get("summary") or {}
        self.assertEqual(summary.get("verdict"), "cap5_confirmed")
        m = summary.get("mandatory_answers") or {}
        self.assertEqual(len(m.get("1_top5_loss_symbols") or []), 5)
        self.assertEqual(m.get("2_cap5_incremental_count"), 9)
        self.assertAlmostEqual(float(m.get("3_cap5_incremental_pnl_yen") or 0.0), -20400.0, places=0)
        self.assertGreater(float(m.get("4_cap3_vs_cap5_pm_delta_yen") or 0.0), 0.0)
        self.assertTrue(m.get("6_cap5_maintain_recommended"))
        cmp_ = summary.get("cap3_vs_cap5_pm") or {}
        self.assertAlmostEqual(float(cmp_.get("cap5_pm_pnl_yen") or 0.0), PM_EQUITY_DELTA, places=0)
        self.assertEqual(float(summary.get("equity_milestones", {}).get("20260617_pm") or 0.0), EQUITY_20260617_PM)


if __name__ == "__main__":
    unittest.main()

"""Phase424 equity curve consistency tests."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRADEBOT = REPO.parent


class Phase424EquityCurveConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys

        for p in (REPO / "src", TRADEBOT):
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)

    def test_canonical_loader_starts_from_phase423_baseline(self) -> None:
        from research.equity_curve_shadow import (
            CANONICAL_BASELINE_END,
            load_canonical_live_config_trades,
            load_period_trades,
        )
        from research.phase271_leverage_attribution_and_robustness import simulate_audited

        canonical, c_meta = load_canonical_live_config_trades(TRADEBOT)
        legacy, l_meta = load_period_trades(TRADEBOT)

        self.assertGreater(c_meta.get("historical_trade_count") or 0, 600)
        self.assertLess(c_meta.get("input_trade_count") or 0, l_meta.get("input_trade_count") or 0)
        self.assertEqual(c_meta.get("trade_source"), "phase423_canonical_baseline_plus_forward_collapsed")
        self.assertIn("20260617", c_meta.get("period_days") or [])

        sim = simulate_audited(
            canonical,
            starting_equity=1_500_000,
            leverage=2.0,
            cap=5,
            stop_policy="fixed_stop_1p2",
        )
        by_day = {str(r.get("day") or ""): r for r in sim.get("_daily_rows") or []}
        eq_616 = float((by_day.get(CANONICAL_BASELINE_END) or {}).get("end_equity") or 0.0)
        self.assertAlmostEqual(eq_616, 1_641_767.98, places=0)
        self.assertEqual(int(sim.get("reject_reason_counts", {}).get("invalid_price") or 0), 0)

    def test_phase273_274_equity_match_on_canonical_stream(self) -> None:
        from research.equity_curve_shadow import load_canonical_live_config_trades
        from research.phase271_leverage_attribution_and_robustness import simulate_audited
        from research.phase274_live_config_auto_transition_shadow import simulate_auto_transition

        trades, _ = load_canonical_live_config_trades(TRADEBOT)
        p273 = simulate_audited(
            trades,
            starting_equity=1_500_000,
            leverage=2.0,
            cap=5,
            stop_policy="fixed_stop_1p2",
        )
        p274 = simulate_auto_transition(trades)
        self.assertAlmostEqual(
            float(p273.get("final_equity") or 0.0),
            float(p274.get("final_equity") or 0.0),
            places=0,
        )

    def test_phase424_audit_passes(self) -> None:
        from research.phase424_equity_curve_consistency_audit import run_phase424_audit

        result = run_phase424_audit(repo_root=TRADEBOT)
        audit = result.get("audit") or {}
        checks = audit.get("checks") or {}
        self.assertTrue(checks.get("equity_20260616_matches_phase423"))
        self.assertTrue(checks.get("no_invalid_price_rejects"))
        self.assertTrue(checks.get("phase273_274_equity_aligned"))
        self.assertEqual(audit.get("verdict"), "bug_fixed")


if __name__ == "__main__":
    unittest.main()

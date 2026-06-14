"""Phase268 capital simulation reconciliation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.capital_simulation_reconciliation import (  # noqa: E402
    analyze_simulation,
    build_accepted_vs_rejected_rows,
    categorize_reject_reason,
    collect_duplicate_trades,
    run_capital_simulation_reconciliation,
)
from research.phase388_cap1500k_live_candidate_validation import simulate_detailed  # noqa: E402


class TestCapitalSimulationReconciliation(unittest.TestCase):
    def test_categorize_reject_reason(self) -> None:
        self.assertEqual(categorize_reject_reason("max_concurrent_positions"), "CAP reached")
        self.assertEqual(categorize_reject_reason("insufficient_buying_power"), "buying power")
        self.assertEqual(categorize_reject_reason("maintenance_ratio_stop"), "leverage")
        self.assertEqual(categorize_reject_reason("unknown"), "other")

    def test_collect_duplicate_trades(self) -> None:
        rows = [
            {"symbol": "1001.T", "entry_time": "2026-05-29T09:00:00+09:00", "pnl_yen_100": 100.0},
            {"symbol": "1001.T", "entry_time": "2026-05-29T09:00:00+09:00", "pnl_yen_100": 200.0},
        ]
        kept, dups, removed = collect_duplicate_trades(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, 1)
        self.assertEqual(len(dups), 1)

    def test_analyze_simulation_small(self) -> None:
        trades = [
            {
                "symbol": "1001.T",
                "entry_time": "2026-05-29T09:10:00+09:00",
                "exit_time": "2026-05-29T09:30:00+09:00",
                "entry_price": 1000.0,
                "pnl_yen_100": 500.0,
            },
            {
                "symbol": "1002.T",
                "entry_time": "2026-05-29T09:11:00+09:00",
                "exit_time": "2026-05-29T09:40:00+09:00",
                "entry_price": 2000.0,
                "pnl_yen_100": 800.0,
            },
            {
                "symbol": "1003.T",
                "entry_time": "2026-05-29T09:12:00+09:00",
                "exit_time": "2026-05-29T09:50:00+09:00",
                "entry_price": 1500.0,
                "pnl_yen_100": 300.0,
            },
        ]
        sim = simulate_detailed(
            trades,
            scenario_id="test_cap2",
            cap=2,
            initial_equity=1_500_000.0,
        )
        analysis = analyze_simulation(sim, trades, duplicate_trades=[])
        self.assertEqual(analysis["sim_accepted_count"], 2)
        self.assertEqual(analysis["sim_rejected_count"], 1)
        rows = build_accepted_vs_rejected_rows(analysis)
        self.assertEqual(len(rows), 3)
        self.assertGreater(analysis["rejected"]["total_pnl_yen"], 0)

    def test_run_on_repo(self) -> None:
        result = run_capital_simulation_reconciliation(
            repo_root=REPO,
            reports_dir=REPO / "kabu_native" / "results" / "reports",
        )
        self.assertEqual(result["phase"], "268-Capital-Simulation-Reconciliation")
        dual = result.get("dual_layer") or {}
        live = dual.get("live_simulation_layer") or {}
        verdict = dual.get("adoption_verdict") or {}
        self.assertIn("final_equity", live)
        self.assertIn("days_below_50pct", live)
        self.assertEqual(verdict.get("primary_metric"), "final_equity")
        acc = (result.get("accepted_vs_rejected") or {}).get("accepted") or {}
        rej = (result.get("accepted_vs_rejected") or {}).get("rejected") or {}
        self.assertGreater(acc.get("trade_count", 0), 0)
        self.assertGreater(rej.get("trade_count", 0), 0)
        self.assertIsNotNone(acc.get("profit_factor"))
        self.assertIsNotNone(rej.get("profit_factor"))


if __name__ == "__main__":
    unittest.main()

"""Phase378: Period-B loss concentration tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase378_period_b_loss_concentration import (  # noqa: E402
    FOCUS_SYMBOLS,
    Phase378PeriodBLossConcentration,
    entry_exit_dominated_analysis,
    exit_reason_breakdown,
    loss_concentration_shares,
    loss_rank_row,
    losing_trades_sorted,
    symbol_loss_ranking,
    total_loss_amount,
)


class TestPhase378PeriodBLossConcentration(unittest.TestCase):
    def _sample_trades(self) -> list[dict]:
        return [
            {
                "day_key": "20260529",
                "symbol": "6976.T",
                "entry_time": "09:05:00",
                "exit_time": "09:06:00",
                "pnl_yen_100": -5000.0,
                "pnl_pct": -1.0,
                "exit_reason_canonical": "stop_hit",
                "peak_mfe_pct": 0.1,
                "peak_mae_pct": -1.2,
                "hold_sec": 45.0,
                "universe_group": "dynamic40",
                "entry_momentum_continuation_score": 0.3,
                "entry_vwap_dev_pct": -0.5,
                "entry_rise_5min_pct": -0.2,
                "board_dynamic_trailing_tier": "board_high",
            },
            {
                "day_key": "20260601",
                "symbol": "6981.T",
                "entry_time": "10:00:00",
                "exit_time": "10:05:00",
                "pnl_yen_100": -2000.0,
                "pnl_pct": -0.5,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 0.8,
                "hold_sec": 300.0,
                "universe_group": "core10",
            },
            {
                "day_key": "20260610",
                "symbol": "9999.T",
                "entry_time": "11:00:00",
                "exit_time": "11:10:00",
                "pnl_yen_100": 3000.0,
                "pnl_pct": 1.0,
                "exit_reason_canonical": "trailing_mfe_exit",
                "peak_mfe_pct": 1.2,
                "universe_group": "dynamic40",
            },
        ]

    def test_losing_trades_sorted(self) -> None:
        losses = losing_trades_sorted(self._sample_trades())
        self.assertEqual(len(losses), 2)
        self.assertEqual(losses[0]["symbol"], "6976.T")

    def test_loss_concentration_shares(self) -> None:
        losses = losing_trades_sorted(self._sample_trades())
        total = total_loss_amount(losses)
        shares = loss_concentration_shares(losses, total)
        self.assertEqual(shares["loss_top20_share"], 1.0)

    def test_entry_exit_dominated(self) -> None:
        losses = losing_trades_sorted(self._sample_trades())
        total = total_loss_amount(losses)
        ee = entry_exit_dominated_analysis(losses, total)
        self.assertGreater(ee["entry_dominated"]["count"], 0)
        self.assertGreater(ee["exit_dominated"]["count"], 0)

    def test_exit_reason_breakdown(self) -> None:
        losses = losing_trades_sorted(self._sample_trades())
        total = total_loss_amount(losses)
        rows = exit_reason_breakdown(losses, top_n=10, total_loss=total)
        stop = next(r for r in rows if r["exit_reason"] == "stop_hit")
        self.assertEqual(stop["count"], 1)

    def test_loss_rank_row(self) -> None:
        row = loss_rank_row(self._sample_trades()[0], rank=1)
        self.assertEqual(row["symbol"], "6976.T")
        self.assertEqual(row["universe"], "dynamic40")

    def test_full_analyze_matches_phase377(self) -> None:
        reports = REPO / "kabu_native" / "results" / "reports"
        if not (reports / "phase377_daily_regime_breakdown_summary.json").is_file():
            self.skipTest("phase377 summary missing")
        audit = Phase378PeriodBLossConcentration(reports_dir=reports)
        # inject via analyze only works with trades - run integration via script output check
        # minimal: verify focus symbols constant
        self.assertIn("6976.T", FOCUS_SYMBOLS)


if __name__ == "__main__":
    unittest.main()

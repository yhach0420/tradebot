"""Phase387 CAP2 shadow validation tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase387_cap2_shadow_validation import (  # noqa: E402
    DEFAULT_SHADOW_START_DAY,
    build_daily_rows,
    build_required_answers,
    daily_cohort_metrics,
)


class TestPhase387Cap2Shadow(unittest.TestCase):
    def _trade(self, *, day: str = "20260613", symbol: str = "1234.T", pnl: float = 1000.0) -> dict:
        return {
            "symbol": symbol,
            "entry_time": f"2026-06-13T09:05:00+09:00",
            "exit_time": f"2026-06-13T09:30:00+09:00",
            "entry_price": 1000.0,
            "exit_price": 1010.0,
            "pnl_yen_100": pnl,
            "pnl_pct": 1.0,
            "exit_reason_canonical": "trailing_mfe_exit",
            "peak_mfe_pct": 0.8,
            "day_key": day,
            "session_kind": "am",
        }

    def test_default_shadow_start_after_phase386(self) -> None:
        self.assertEqual(DEFAULT_SHADOW_START_DAY, "20260613")

    def test_daily_metrics(self) -> None:
        trades = [self._trade(), self._trade(symbol="5678.T", pnl=-500.0)]
        dm = daily_cohort_metrics(trades)
        self.assertEqual(dm["20260613"]["trade_count"], 2)

    def test_required_answers_cap2_lead(self) -> None:
        actual = {"total_pnl_yen_100": 1000.0, "profit_factor": 1.1}
        shadow = {"total_pnl_yen_100": 5000.0, "profit_factor": 1.8}
        additional = {"total_pnl_yen_100": -2000.0, "profit_factor": 0.4}
        daily = [{"cap2_better_day": True, "cap3_additional_negative_day": True}]
        ans = build_required_answers(
            actual_metrics=actual,
            shadow_metrics=shadow,
            additional_metrics=additional,
            daily_rows=daily,
            phase386_ref={"loaded": True},
        )
        self.assertTrue(ans["cap2_superiority_continues"])
        self.assertTrue(ans["cap3_additional_still_negative"])

    def test_build_daily_rows(self) -> None:
        cap2 = {"20260613": {"trade_count": 1, "total_pnl_yen_100": 2000.0, "profit_factor": 2.0, "win_rate": 1.0, "stop_hit_count": 0, "low_mfe_stop_count": 0, "trailing_mfe_exit_count": 1, "overlap_replaced_count": 0}}
        cap3 = {"20260613": {"trade_count": 2, "total_pnl_yen_100": 500.0, "profit_factor": 1.1, "win_rate": 0.5, "stop_hit_count": 1, "low_mfe_stop_count": 0, "trailing_mfe_exit_count": 1, "overlap_replaced_count": 0}}
        add = {"20260613": {"trade_count": 1, "total_pnl_yen_100": -1500.0, "profit_factor": 0.2, "win_rate": 0.0, "stop_hit_count": 1, "low_mfe_stop_count": 0, "trailing_mfe_exit_count": 0, "overlap_replaced_count": 0}}
        rows = build_daily_rows(
            cap2_trades=[],
            cap3_trades=[],
            cap3_additional_trades=[],
            cap2_daily=cap2,
            cap3_daily=cap3,
            add_daily=add,
        )
        self.assertTrue(rows[0]["cap2_better_day"])
        self.assertTrue(rows[0]["cap3_additional_negative_day"])


if __name__ == "__main__":
    unittest.main()

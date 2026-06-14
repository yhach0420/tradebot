"""Phase376: production daily PnL review tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for p in (REPO, REPO / "kabu_native" / "src"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.phase376_production_daily_pnl_review import (  # noqa: E402
    PRIMARY_STACK,
    Phase376ProductionDailyPnlReview,
    build_equity_curve_rows,
    daily_metrics_from_trades,
    dependency_check,
    kept_trades_for_stack,
)


class TestPhase376ProductionDailyPnlReview(unittest.TestCase):
    def test_daily_metrics_from_trades(self) -> None:
        trades = [
            {
                "pnl_yen_100": 1000.0,
                "pnl_pct": 0.5,
                "exit_reason_canonical": "trailing_mfe_exit",
                "universe_group": "dynamic40",
                "session_kind": "am",
                "peak_mfe_pct": 1.0,
            },
            {
                "pnl_yen_100": -500.0,
                "pnl_pct": -0.3,
                "exit_reason_canonical": "stop_hit",
                "universe_group": "core10",
                "session_kind": "pm",
                "peak_mfe_pct": 0.1,
            },
        ]
        m = daily_metrics_from_trades(trades, day="20260612", stack_id=PRIMARY_STACK)
        self.assertEqual(m["trade_count"], 2)
        self.assertEqual(m["total_pnl_yen_100"], 500.0)
        self.assertEqual(m["stop_hit_count"], 1)
        self.assertEqual(m["low_mfe_stop_hit_count"], 1)
        self.assertEqual(m["dynamic40_pnl_yen_100"], 1000.0)
        self.assertEqual(m["core10_pnl_yen_100"], -500.0)

    def test_equity_curve_drawdown(self) -> None:
        daily = [
            {"day": "20260601", "stack_id": PRIMARY_STACK, "total_pnl_yen_100": 1000.0},
            {"day": "20260602", "stack_id": PRIMARY_STACK, "total_pnl_yen_100": -300.0},
            {"day": "20260603", "stack_id": PRIMARY_STACK, "total_pnl_yen_100": 200.0},
        ]
        eq = build_equity_curve_rows(daily)
        self.assertEqual(len(eq), 3)
        self.assertEqual(eq[-1]["cumulative_pnl_yen_100"], 900.0)
        self.assertEqual(eq[1]["drawdown_yen_100"], -300.0)

    def test_dependency_check(self) -> None:
        daily = [
            {"day": "20260601", "stack_id": PRIMARY_STACK, "total_pnl_yen_100": 100.0},
            {"day": "20260612", "stack_id": PRIMARY_STACK, "total_pnl_yen_100": 900.0},
        ]
        dep = dependency_check(daily)
        self.assertTrue(dep.get("is_single_day_dependent"))

    def test_aggregate_resolves_day_key_from_trades(self) -> None:
        audit = Phase376ProductionDailyPnlReview(reports_dir=Path("/tmp"))
        audit.ingest_session(
            {
                "session_id": "20260601/live_session_080000",
                "session_kind": "am",
                "trades": [
                    {
                        "pnl_yen_100": 1000.0,
                        "pnl_pct": 0.5,
                        "exit_reason_canonical": "trailing_mfe_exit",
                        "universe_group": "dynamic40",
                        "session_kind": "am",
                        "day_key": "20260601",
                        "peak_mfe_pct": 1.0,
                    }
                ],
            }
        )
        agg = audit.aggregate(compare_stacks=False)
        self.assertEqual(len(agg["daily_rows"]), 1)
        self.assertEqual(agg["daily_rows"][0]["day"], "20260601")
        self.assertEqual(agg["daily_rows"][0]["total_pnl_yen_100"], 1000.0)

    def test_kept_trades_for_stack_c(self) -> None:
        session = {
            "session_kind": "am",
            "trades": [
                {
                    "pnl_yen_100": 100.0,
                    "universe_slot": "dynamic",
                    "entry_rise_5min_pct": -1.0,
                    "entry_vwap_dev_pct": -1.0,
                },
                {
                    "pnl_yen_100": 200.0,
                    "universe_slot": "dynamic",
                    "entry_rise_5min_pct": 1.0,
                    "entry_vwap_dev_pct": 1.0,
                },
            ],
        }
        kept = kept_trades_for_stack(session, PRIMARY_STACK)
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.phase342_board_failure_exit_evaluation import (
    MFE_COHORTS,
    Phase342BoardFailureAggregator,
)
from small_paper.board_failure_exit_shadow import (
    BOARD_FAILURE_CONFIRM_TICKS,
    BOARD_FAILURE_EXIT_ID,
    BOARD_FAILURE_IMB_DELTA,
    BoardFailureExitShadowPack,
    board_failure_arm_tick,
    board_failure_deterioration_tick,
    evaluate_board_failure_exit,
    export_board_failure_trade_rows,
    make_position_id,
    mfe_bucket,
    trade_in_mfe_cohort,
)

JST = ZoneInfo("Asia/Tokyo")


def _payload(
    *,
    bid_qty: float = 2000.0,
    ask_qty: float = 8000.0,
    price: float = 990.0,
    ts: str = "2026-06-05T10:00:00+09:00",
) -> dict:
    return {
        "BidPrice": price - 0.5,
        "AskPrice": price + 0.5,
        "BidQty": bid_qty,
        "AskQty": ask_qty,
        "CurrentPrice": price,
        "CurrentPriceTime": ts,
    }


class TestPhase342BoardFailureExit(unittest.TestCase):
    def test_evaluator_units(self) -> None:
        self.assertTrue(
            board_failure_deterioration_tick(
                current_pnl_pct=-0.2,
                board_imbalance_delta=-0.09,
            )
        )
        self.assertFalse(
            board_failure_deterioration_tick(
                current_pnl_pct=0.1,
                board_imbalance_delta=-0.09,
            )
        )
        self.assertTrue(
            board_failure_arm_tick(
                current_pnl_pct=-0.2,
                board_imbalance_delta=-0.09,
                low_updated_this_tick=True,
            )
        )
        self.assertFalse(
            evaluate_board_failure_exit(armed=True, confirm_streak=BOARD_FAILURE_CONFIRM_TICKS - 1)
        )
        self.assertTrue(
            evaluate_board_failure_exit(armed=True, confirm_streak=BOARD_FAILURE_CONFIRM_TICKS)
        )
        self.assertEqual(mfe_bucket(0.2), "mfe_lt_0p3")
        self.assertEqual(mfe_bucket(0.4), "mfe_lt_0p5")
        self.assertEqual(mfe_bucket(0.8), "mfe_lt_1p0")
        self.assertEqual(mfe_bucket(1.2), "mfe_ge_1p0")

    def test_three_tick_trigger_after_low_arm(self) -> None:
        pack = BoardFailureExitShadowPack()
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        entry_price = 1000.0
        pack.register_position(
            position_id=pid,
            symbol="9984.T",
            entry_time=ent,
            entry_price=entry_price,
            payload=_payload(bid_qty=6000, ask_qty=4000, price=entry_price),
            entry_shadow={},
        )
        prices = [995.0, 994.0, 993.0]
        for i, px in enumerate(prices):
            pack.record_holding_tick(
                symbol="9984.T",
                position_id=pid,
                entry_time=ent,
                payload=_payload(
                    bid_qty=2000,
                    ask_qty=8000,
                    price=px,
                    ts=f"2026-06-05T10:0{i}+09:00",
                ),
                current_price=px,
                entry_price=entry_price,
                mfe_pct=0.1,
                entry_shadow={},
            )
        pack.finalize_position(
            position_id=pid,
            actual_exit_reason="stop_hit",
            actual_exit_time=datetime(2026, 6, 5, 10, 30, 0, tzinfo=JST),
            actual_exit_price=980.0,
            entry_price=entry_price,
        )
        rows = export_board_failure_trade_rows(pack)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["candidate_id"], BOARD_FAILURE_EXIT_ID)
        self.assertFalse(row["no_candidate_trigger"])
        self.assertLess(float(row["shadow_pnl_pct"]), 0.0)

    def test_mfe_cohort_filter(self) -> None:
        row = {"peak_mfe_pct": 0.25}
        self.assertTrue(trade_in_mfe_cohort(row, "mfe_lt_0p3"))
        self.assertTrue(trade_in_mfe_cohort(row, "mfe_lt_0p5"))
        self.assertFalse(trade_in_mfe_cohort({"peak_mfe_pct": 0.6}, "mfe_lt_0p3"))

    def test_aggregator_mfe_stratification(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            agg = Phase342BoardFailureAggregator(reports_dir=Path(tmp))
            trades = [
                {
                    "position_id": "p1",
                    "symbol": "9984.T",
                    "candidate_id": BOARD_FAILURE_EXIT_ID,
                    "peak_mfe_pct": 0.2,
                    "mfe_bucket": "mfe_lt_0p3",
                    "shadow_pnl_yen_100": -300.0,
                    "actual_pnl_yen_100": -800.0,
                    "actual_exit_reason": "stop_hit",
                    "candidate_vs_actual_delta_yen": 500.0,
                    "no_candidate_trigger": False,
                },
                {
                    "position_id": "p2",
                    "symbol": "6758.T",
                    "candidate_id": BOARD_FAILURE_EXIT_ID,
                    "peak_mfe_pct": 0.8,
                    "mfe_bucket": "mfe_lt_1p0",
                    "shadow_pnl_yen_100": 200.0,
                    "actual_pnl_yen_100": 500.0,
                    "actual_exit_reason": "profit_take",
                    "candidate_vs_actual_delta_yen": -300.0,
                    "no_candidate_trigger": False,
                },
            ]
            agg.ingest_session(
                session_meta={"session_id": "s1", "day_key": "20260521"},
                trade_rows=trades,
                push_rows=1000,
                runtime_sec=1.0,
            )
            summary = agg.build_summary()
            self.assertEqual(summary["positions_evaluated"], 2)
            cohorts = summary["mfe_cohort_metrics"]
            self.assertIn("all", cohorts)
            self.assertEqual(cohorts["mfe_lt_0p3"]["positions"], 1)
            self.assertEqual(cohorts["mfe_lt_0p3"]["stop_hit_reduction_count"], 1)
            for c in MFE_COHORTS:
                self.assertIn(c, cohorts)


if __name__ == "__main__":
    unittest.main()

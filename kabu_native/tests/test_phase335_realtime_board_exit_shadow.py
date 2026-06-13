import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.realtime_board_exit_shadow import (
    TICK_FIELD_KEYS,
    TRADE_FIELD_KEYS,
    RealtimeBoardExitShadowLogger,
    evaluate_extend_candidate,
    evaluate_shadow_exit_reason,
    evaluate_shadow_watches,
    make_position_id,
)

JST = ZoneInfo("Asia/Tokyo")


def _sample_payload(*, bid_qty: float = 6000.0, ask_qty: float = 4000.0) -> dict:
    return {
        "BidPrice": 1000.0,
        "AskPrice": 1001.0,
        "BidQty": bid_qty,
        "AskQty": ask_qty,
        "Buy1": {"Price": 1000.0, "Qty": bid_qty},
        "Sell1": {"Price": 1001.0, "Qty": ask_qty},
        "CurrentPrice": 1000.5,
        "CurrentPriceTime": "2026-06-05T10:00:00+09:00",
    }


class TestPhase335RealtimeBoardExitShadow(unittest.TestCase):
    def test_make_position_id(self) -> None:
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        self.assertEqual(make_position_id("9984.T", ent), "9984.T_20260605T100000")

    def test_evaluate_shadow_watches(self) -> None:
        watches = evaluate_shadow_watches(
            entry_bid_ask_imbalance=0.60,
            current_board_imbalance=0.50,
            mfe_pct=0.8,
            current_pnl_pct=0.2,
        )
        self.assertTrue(watches["profit_protect_watch"])
        self.assertFalse(watches["loss_acceleration_watch"])

        hold = evaluate_shadow_watches(
            entry_bid_ask_imbalance=0.55,
            current_board_imbalance=0.56,
            mfe_pct=1.2,
            current_pnl_pct=0.8,
        )
        self.assertTrue(hold["board_strength_hold_watch"])

    def test_shadow_exit_priority(self) -> None:
        loss = evaluate_shadow_exit_reason(
            board_imbalance_delta=-0.10,
            mfe_pct=0.2,
            current_pnl_pct=-0.5,
        )
        self.assertEqual(loss, "loss_acceleration_exit")
        collapse = evaluate_shadow_exit_reason(
            board_imbalance_delta=-0.10,
            mfe_pct=0.8,
            current_pnl_pct=0.3,
        )
        self.assertEqual(collapse, "board_collapse_profit_exit")
        protect = evaluate_shadow_exit_reason(
            board_imbalance_delta=-0.06,
            mfe_pct=0.7,
            current_pnl_pct=0.2,
        )
        self.assertEqual(protect, "profit_protect_exit")
        self.assertTrue(
            evaluate_extend_candidate(board_imbalance_delta=0.02, mfe_pct=1.1)
        )

    def test_shadow_exit_once_per_position(self) -> None:
        logger = RealtimeBoardExitShadowLogger()
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        logger.register_position(
            position_id=pid,
            symbol="9984.T",
            entry_time=ent,
            entry_price=1000.0,
            payload=_sample_payload(bid_qty=6000.0, ask_qty=4000.0),
            entry_shadow={},
        )
        deteriorated = _sample_payload(bid_qty=2000.0, ask_qty=8000.0)
        logger.record_holding_tick(
            symbol="9984.T",
            position_id=pid,
            entry_time=ent,
            payload=deteriorated,
            current_price=1005.0,
            entry_price=1000.0,
            mfe_pct=0.7,
            entry_shadow={},
        )
        self.assertEqual(
            logger._shadow_states[pid].shadow_exit_reason, "board_collapse_profit_exit"
        )
        logger.record_holding_tick(
            symbol="9984.T",
            position_id=pid,
            entry_time=ent,
            payload=deteriorated,
            current_price=1003.0,
            entry_price=1000.0,
            mfe_pct=0.7,
            entry_shadow={},
        )
        self.assertEqual(logger.shadow_exit_reason_counts["board_collapse_profit_exit"], 1)

    def test_logger_push_and_holding_coverage(self) -> None:
        logger = RealtimeBoardExitShadowLogger()
        payload = _sample_payload()
        logger.record_push_board_tick(symbol="9984.T", payload=payload)
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("9984.T", ent)
        logger.record_holding_tick(
            symbol="9984.T",
            position_id=pid,
            entry_time=ent,
            payload=payload,
            current_price=1000.5,
            entry_price=1000.0,
            mfe_pct=0.7,
            entry_shadow={
                "entry_order_book_imbalance": 0.60,
                "entry_imbalance_percentile": 55.0,
            },
        )
        summary = logger.build_lite_summary()
        self.assertEqual(summary["board_tick_received_count"], 1)
        self.assertEqual(summary["board_tick_with_bid_ask_qty_count"], 1)
        self.assertEqual(summary["board_tick_bid_ask_qty_coverage_pct"], 100.0)
        self.assertEqual(summary["holding_board_tick_count"], 1)
        self.assertEqual(summary["holding_board_tick_coverage_pct"], 100.0)

    def test_finalize_position_backfills_ticks(self) -> None:
        logger = RealtimeBoardExitShadowLogger()
        payload = _sample_payload(bid_qty=2000.0, ask_qty=8000.0)
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        pid = make_position_id("7203.T", ent)
        logger.record_holding_tick(
            symbol="7203.T",
            position_id=pid,
            entry_time=ent,
            payload=payload,
            current_price=990.0,
            entry_price=1000.0,
            mfe_pct=0.2,
            entry_shadow={"entry_order_book_imbalance": 0.55, "entry_imbalance_percentile": 50.0},
        )
        exit_time = datetime(2026, 6, 5, 10, 5, 0, tzinfo=JST)
        logger.finalize_position(
            position_id=pid,
            actual_exit_reason="trailing_mfe_exit",
            actual_exit_time=exit_time,
            actual_exit_price=995.0,
            entry_price=1000.0,
        )
        self.assertEqual(logger._ticks[0]["actual_exit_reason"], "trailing_mfe_exit")
        self.assertIsNotNone(logger._ticks[0]["actual_pnl_yen_100"])

    def test_write_outputs(self) -> None:
        logger = RealtimeBoardExitShadowLogger()
        logger.record_push_board_tick(symbol="9984.T", payload=_sample_payload())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "kabu_native" / "results" / "reports"
            paths = logger.write_outputs(reports, day_stamp="20260605")
            self.assertTrue(paths["ticks"].endswith("phase335_lite_realtime_board_shadow_ticks_20260605.csv"))
            lite = json.loads(Path(paths["lite_summary"]).read_text(encoding="utf-8"))
            summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
            self.assertEqual(lite["variant"], "lite")
            self.assertEqual(summary["phase"], 335)
            self.assertEqual(summary["variant"], "full")
            for key in (
                "actual_exit_count",
                "shadow_exit_count",
                "shadow_exit_reason_counts",
                "actual_total_pnl_yen_100",
                "shadow_total_pnl_yen_100",
                "no_shadow_exit_count",
            ):
                self.assertIn(key, summary)
            for key in TRADE_FIELD_KEYS:
                self.assertTrue(paths["trades"].endswith(".csv"))

    def test_tick_field_keys_complete(self) -> None:
        logger = RealtimeBoardExitShadowLogger()
        ent = datetime(2026, 6, 5, 10, 0, 0, tzinfo=JST)
        logger.record_holding_tick(
            symbol="9984.T",
            position_id=make_position_id("9984.T", ent),
            entry_time=ent,
            payload=_sample_payload(),
            current_price=1000.0,
            entry_price=1000.0,
            mfe_pct=0.0,
            entry_shadow={"entry_order_book_imbalance": 0.5},
        )
        for key in TICK_FIELD_KEYS:
            self.assertIn(key, logger._ticks[0])

    def test_observer_wires_board_shadow(self) -> None:
        from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        logger = RealtimeBoardExitShadowLogger()
        now = datetime.now(JST)
        tracker = ObserverPositionTracker(
            ObserverTrackerConfig(
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            ),
            board_exit_shadow=logger,
        )
        trade = {
            "symbol": "9984.T",
            "profile": "test",
            "entry_time": now.isoformat(timespec="seconds"),
            "exit_time": now.isoformat(timespec="seconds"),
            "continuation_quality_score": 0.8,
            "continuation_quality": 0.8,
        }
        tracker.register_entry(
            trade=trade,
            payload=_sample_payload(),
            quality_tier="A",
            entry_price=1000.0,
        )
        events = tracker.on_tick(
            symbol="9984.T",
            trade=trade,
            payload=_sample_payload(bid_qty=3000.0, ask_qty=7000.0),
            current_price=1000.0,
            session_bucket="am",
        )
        self.assertEqual(logger.holding_board_tick_count, 1)
        self.assertEqual([e.kind for e in events], [])


if __name__ == "__main__":
    unittest.main()

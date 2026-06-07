"""Phase299: entry_order_book_imbalance must be set before gate score on all paths."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from research.exposure_gate import REJECT_ENTRY_SCORE_V2_BELOW, GateDecision
from small_paper.board_imbalance_shadow import (
    board_mid_token_active,
    compute_board_imbalance_shadow_fields,
    compute_entry_order_book_imbalance_field,
)
from small_paper.entry_expectancy_score_shadow import (
    SCORE_POINTS_V2,
    _feature_token,
    compute_entry_expectancy_score_fields,
)
from small_paper.pilot_runner import _event_from_gate

JST = ZoneInfo("Asia/Tokyo")


def _board_payload(*, bid: float, ask: float) -> dict:
    return {"BidQty": bid, "AskQty": ask}


class TestPhase299BoardPregateFix(unittest.TestCase):
    def test_pregate_field_not_none_with_board(self) -> None:
        out = compute_entry_order_book_imbalance_field(payload=_board_payload(bid=48, ask=52))
        self.assertIsNotNone(out["entry_order_book_imbalance"])
        self.assertIsInstance(out["entry_board_mid_token_active"], bool)

    def test_pregate_matches_shadow_imbalance(self) -> None:
        payload = _board_payload(bid=40, ask=60)
        trade = {"trading_value": 2e10, "entry_vwap_dev_pct": 1.0}
        pre = compute_entry_order_book_imbalance_field(payload=payload)
        full = compute_board_imbalance_shadow_fields(
            trade=trade,
            payload=payload,
            session_imbalance_samples=[],
        )
        self.assertEqual(pre["entry_order_book_imbalance"], full["entry_order_book_imbalance"])
        self.assertEqual(pre["entry_board_mid_token_active"], full["entry_board_mid_token_active"])

    def test_board_mid_token_active_mid_tertile(self) -> None:
        # 0.48 is within Board mid tertile (p33=0.437286, p66=0.527869)
        self.assertTrue(board_mid_token_active(0.48))
        self.assertFalse(board_mid_token_active(0.60))

    def test_reject_event_persists_entry_order_book_imbalance(self) -> None:
        trade = {
            "profile": "momentum_volume_v13_combined",
            "symbol": "9984.T",
            "entry_time": datetime(2026, 6, 4, 9, 30, tzinfo=JST).isoformat(),
            "exit_time": datetime(2026, 6, 4, 10, 0, tzinfo=JST).isoformat(),
            "continuation_quality_score": 0.5,
            "entry_high_break_recent": False,
            "max_continuation_duration": 10,
            "momentum_continuation_score": 0.2,
            "current_price": 5000.0,
            "trading_value": 1e10,
            "rolling_mae_pct": 0.0,
            "entry_order_book_imbalance": 0.48,
            "entry_board_mid_token_active": True,
        }
        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        decision = GateDecision(
            accept=False,
            reason=REJECT_ENTRY_SCORE_V2_BELOW,
            continuation_quality_score=0.5,
            quality_tier="",
            entry_expectancy_score_v2=trade.get("entry_expectancy_score_v2"),
            entry_score_v2_threshold=5,
            entry_score_v2_gate_pass=False,
        )
        ev = _event_from_gate(
            event_type="rejected",
            trade=trade,
            decision=decision,
            source="live",
            message_index=1,
            current_price=5000.0,
        )
        self.assertIn("entry_order_book_imbalance", ev)
        self.assertIsNotNone(ev["entry_order_book_imbalance"])
        self.assertTrue(ev["entry_board_mid_token_active"])

    def test_board_mid_contributes_pre_gate(self) -> None:
        base = {
            "entry_high_break_recent": False,
            "max_continuation_duration": 10,
            "momentum_continuation_score": 0.20,
            "current_price": 5000.0,
            "trading_value": 3e10,
            "rolling_mae_pct": -0.0003,
        }
        without = {**base, "entry_order_book_imbalance": None}
        with_board = {**base, "entry_order_book_imbalance": 0.48}
        score_without = int(
            compute_entry_expectancy_score_fields(trade=without)["entry_expectancy_score_v2"]
        )
        score_with = int(
            compute_entry_expectancy_score_fields(trade=with_board)["entry_expectancy_score_v2"]
        )
        self.assertGreater(score_with, score_without)
        self.assertEqual(_feature_token("Board", with_board), "Board:mid")
        self.assertIn("Board:mid", SCORE_POINTS_V2)


if __name__ == "__main__":
    unittest.main()

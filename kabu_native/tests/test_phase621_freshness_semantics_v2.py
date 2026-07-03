"""Phase621: freshness semantics v2 runtime tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from small_paper.entry_scan_controller import (
    PRICE_FRESHNESS_CURRENT,
    PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
    REJECT_DATA_STALE_BOARD,
    REJECT_DATA_STALE_PRICE,
    REJECT_EVENT_STALE_PRICE,
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)

JST = ZoneInfo("Asia/Tokyo")


def _tick(*, age_sec: float = 0.5) -> str:
    now = datetime.now(JST)
    return (now - timedelta(seconds=age_sec)).isoformat(timespec="milliseconds")


def _payload(*, price_age: float = 0.5, board_age: float = 0.5, event_age: float = 0.5) -> dict:
    now = datetime.now(JST)
    recorded = (now - timedelta(seconds=event_age)).isoformat(timespec="milliseconds")
    price_tick = (now - timedelta(seconds=price_age)).isoformat(timespec="milliseconds")
    board_tick = (now - timedelta(seconds=board_age)).isoformat(timespec="milliseconds")
    return {
        "CurrentPrice": 1000.0,
        "CurrentPriceTime": price_tick,
        "BidPrice": 999.0,
        "AskPrice": 1001.0,
        "BidQty": 100.0,
        "AskQty": 100.0,
        "BidTime": board_tick,
        "AskTime": board_tick,
        "recorded_at": recorded,
    }


class TestFreshnessSemanticsV2(unittest.TestCase):
    def test_v2_trade_stale_tag_only_passes(self) -> None:
        payload = _payload(price_age=5.0, board_age=0.5, event_age=0.5)
        ref = datetime.now(JST)
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        decision = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=True,
            trade_stale_threshold_sec=10.0,
            reference_now=ref,
        )
        self.assertIsNone(decision.reject_reason)
        self.assertEqual(decision.price_freshness_source, PRICE_FRESHNESS_CURRENT)
        self.assertFalse(decision.trade_stale)

    def test_v2_trade_stale_over_threshold_tags(self) -> None:
        payload = _payload(price_age=12.0, board_age=0.5, event_age=0.5)
        ref = datetime.now(JST)
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        decision = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=True,
            trade_stale_threshold_sec=10.0,
            reference_now=ref,
        )
        self.assertIsNone(decision.reject_reason)
        self.assertEqual(decision.price_freshness_source, PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE)
        self.assertTrue(decision.trade_stale)

    def test_v2_event_stale_rejects(self) -> None:
        payload = _payload(price_age=0.5, board_age=0.5, event_age=5.0)
        ref = datetime.now(JST)
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        decision = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=True,
            event_stale_threshold_sec=3.0,
            reference_now=ref,
        )
        self.assertEqual(decision.reject_reason, REJECT_EVENT_STALE_PRICE)
        self.assertTrue(decision.event_stale)

    def test_v2_board_stale_rejects(self) -> None:
        payload = _payload(price_age=0.5, board_age=5.0, event_age=0.5)
        ref = datetime.now(JST)
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        decision = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=True,
            board_stale_threshold_sec=3.0,
            reference_now=ref,
        )
        self.assertEqual(decision.reject_reason, REJECT_DATA_STALE_BOARD)
        self.assertTrue(decision.board_stale)

    def test_rollback_v1_still_rejects_cpt_3s(self) -> None:
        payload = _payload(price_age=5.0, board_age=0.5, event_age=0.5)
        ref = datetime.now(JST)
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        decision = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=False,
            board_fallback_enabled=False,
            reference_now=ref,
        )
        self.assertEqual(decision.reject_reason, REJECT_DATA_STALE_PRICE)


if __name__ == "__main__":
    unittest.main()

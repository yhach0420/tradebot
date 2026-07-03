"""ENTRY scan batching, freshness guard, and Discord detail fields."""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from small_paper.discord_message_builder import build_entry_detail
from small_paper.entry_scan_controller import (
    PRICE_FRESHNESS_BOARD_FALLBACK,
    PRICE_FRESHNESS_CURRENT,
    REJECT_DATA_STALE_BOARD,
    REJECT_DATA_STALE_PRICE,
    REJECT_MAX_ENTRIES_PER_SCAN,
    EntryScanController,
    PendingEntryCandidate,
    check_entry_data_freshness,
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)

JST = ZoneInfo("Asia/Tokyo")


def _fresh_payload(*, age_sec: float = 0.5) -> dict:
    now = datetime.now(JST)
    tick = (now - timedelta(seconds=age_sec)).isoformat(timespec="milliseconds")
    return {
        "CurrentPrice": 1000.0,
        "CurrentPriceTime": tick,
        "BidQty": 100.0,
        "AskQty": 100.0,
        "BidTime": tick,
        "AskTime": tick,
    }


class TestEntryFreshnessGuard(unittest.TestCase):
    def test_stale_price_rejected(self) -> None:
        snap = compute_entry_freshness(
            {"CurrentPriceTime": "2020-01-01T09:00:00+09:00", "BidTime": "2020-01-01T09:00:00+09:00"},
            pipeline_source="live",
        )
        reason = check_entry_data_freshness(snap, max_price_age_sec=3.0, max_board_age_sec=3.0)
        self.assertEqual(reason, REJECT_DATA_STALE_PRICE)

    def test_missing_board_rejected(self) -> None:
        now = datetime.now(JST).isoformat(timespec="milliseconds")
        snap = compute_entry_freshness({"CurrentPriceTime": now}, pipeline_source="live")
        reason = check_entry_data_freshness(snap, max_price_age_sec=3.0, max_board_age_sec=3.0)
        self.assertEqual(reason, REJECT_DATA_STALE_BOARD)

    def test_fresh_payload_passes(self) -> None:
        snap = compute_entry_freshness(_fresh_payload(age_sec=0.5), pipeline_source="live")
        self.assertIsNone(
            check_entry_data_freshness(snap, max_price_age_sec=3.0, max_board_age_sec=3.0)
        )
        self.assertEqual(snap.data_source, "kabu_push")

    def test_board_fallback_rescues_stale_price_ts(self) -> None:
        now = datetime.now(JST)
        tick = (now - timedelta(seconds=0.5)).isoformat(timespec="milliseconds")
        stale_price = (now - timedelta(seconds=120.0)).isoformat(timespec="milliseconds")
        payload = {
            "CurrentPriceTime": stale_price,
            "CurrentPrice": 1000.0,
            "CalcPrice": 1000.0,
            "BidPrice": 999.0,
            "AskPrice": 1001.0,
            "BidTime": tick,
            "AskTime": tick,
            "BidQty": 100.0,
            "AskQty": 100.0,
        }
        snap = compute_entry_freshness(payload, pipeline_source="live")
        decision = evaluate_entry_data_freshness(
            snap, payload, max_price_age_sec=3.0, max_board_age_sec=3.0
        )
        self.assertIsNone(decision.reject_reason)
        self.assertEqual(decision.price_freshness_source, PRICE_FRESHNESS_BOARD_FALLBACK)
        self.assertTrue(decision.fallback_used)

    def test_board_fallback_rejects_wide_spread(self) -> None:
        now = datetime.now(JST)
        tick = (now - timedelta(seconds=0.5)).isoformat(timespec="milliseconds")
        payload = {
            "CurrentPriceTime": None,
            "CalcPrice": 1000.0,
            "BidPrice": 900.0,
            "AskPrice": 1100.0,
            "BidTime": tick,
            "AskTime": tick,
        }
        snap = compute_entry_freshness(payload, pipeline_source="live")
        decision = evaluate_entry_data_freshness(
            snap, payload, max_price_age_sec=3.0, max_board_age_sec=3.0
        )
        self.assertEqual(decision.reject_reason, REJECT_DATA_STALE_PRICE)
        self.assertIn("spread_above_max", decision.fallback_reject_reason or "")

    def test_current_price_time_fresh_unchanged(self) -> None:
        payload = _fresh_payload(age_sec=0.5)
        snap = compute_entry_freshness(payload, pipeline_source="live")
        decision = evaluate_entry_data_freshness(
            snap, payload, max_price_age_sec=3.0, max_board_age_sec=3.0
        )
        self.assertIsNone(decision.reject_reason)
        self.assertEqual(decision.price_freshness_source, PRICE_FRESHNESS_CURRENT)
        self.assertFalse(decision.fallback_used)


class TestEntryScanBatching(unittest.TestCase):
    def _candidate(self, symbol: str, score: int) -> PendingEntryCandidate:
        return PendingEntryCandidate(
            symbol=symbol,
            trade={
                "symbol": symbol,
                "entry_expectancy_score_v2": score,
                "continuation_quality_score": 0.8,
                "trading_value": 2e10,
            },
            decision=object(),
            payload={"CurrentPrice": 1000.0},
            enriched={},
            msg_i=1,
            freshness=compute_entry_freshness(_fresh_payload(), pipeline_source="live"),
            eval_start_ts="t0",
            eval_end_ts="t1",
            eval_latency_ms=10.0,
            entry_signal_ts="t1",
            entry_signal_mono=0.0,
        )

    def test_max_entries_per_scan_one(self) -> None:
        audit: list[dict] = []
        ctrl = EntryScanController(
            max_entries_per_scan=1,
            scan_window_sec=0.0,
            batch_enabled=True,
            audit_writer=audit.append,
        )
        ctrl.begin_symbol_eval(now_mono=1.0)
        ctrl.queue_accepted_candidate(self._candidate("9984.T", 5))
        ctrl.queue_accepted_candidate(self._candidate("6920.T", 4))
        ctrl.queue_accepted_candidate(self._candidate("5803.T", 3))
        flush = ctrl.maybe_flush_after_eval()
        self.assertIsNotNone(flush)
        assert flush is not None
        self.assertEqual(len(flush.accepted), 1)
        self.assertEqual(flush.accepted[0].symbol, "9984.T")
        self.assertEqual(len(flush.rejected_max_scan), 2)
        notify_rejects = [
            r for r in audit if r.get("audit_type") == "entry_notify" and r.get("reject_reason")
        ]
        self.assertEqual(len(notify_rejects), 2)
        self.assertEqual(notify_rejects[0]["reject_reason"], REJECT_MAX_ENTRIES_PER_SCAN)

    def test_scan_summary_logged(self) -> None:
        audit: list[dict] = []
        ctrl = EntryScanController(
            max_entries_per_scan=1,
            scan_window_sec=0.0,
            batch_enabled=True,
            audit_writer=audit.append,
        )
        ctrl.begin_symbol_eval(now_mono=1.0)
        ctrl.queue_accepted_candidate(self._candidate("A.T", 5))
        ctrl.queue_accepted_candidate(self._candidate("B.T", 4))
        ctrl.flush_pending()
        summaries = [r for r in audit if r.get("audit_type") == "entry_scan_summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["entry_candidates_count"], 2)
        self.assertEqual(summaries[0]["entries_sent_count"], 1)
        self.assertTrue(summaries[0]["same_scan_batch_entry"])


class TestDiscordEntryDetail(unittest.TestCase):
    def test_scan_fields_in_entry_detail(self) -> None:
        detail = build_entry_detail(
            symbol="9984.T",
            entry_price=1000.0,
            stop_price=988.0,
            slot_usage="1/3",
            entry_score_v2=5,
            data={
                "scan_id": "20260612_123945_001",
                "data_source": "kabu_push",
                "price_age_sec": 0.8,
                "board_age_sec": 1.2,
                "signal_to_notify_latency_ms": 420.0,
                "same_scan_rank": "1/4",
                "same_scan_candidates": 4,
            },
        )
        self.assertIn("scan_id: 20260612_123945_001", detail)
        self.assertIn("data_source: kabu_push", detail)
        self.assertIn("price_age_sec: 0.8", detail)
        self.assertIn("latency_ms: 420", detail)
        self.assertIn("same_scan_rank: 1/4", detail)


if __name__ == "__main__":
    unittest.main()

"""Phase687W43B-FIX — FWR persistence, canonical SoT, max_concurrent timeline."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (str(NATIVE / "src"), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from small_paper.canonical_summary import (  # noqa: E402
    collect_canonical_trades,
    enrich_summary_with_canonical,
    peak_concurrent_from_position_events,
    session_close_pnl_breakdown,
)
from small_paper.flat_weak_range_forward_shadow import (  # noqa: E402
    FlatWeakRangeForwardShadowCounters,
    enrich_exit_flat_weak_range_shadow_fields,
)
from small_paper.observer_position_tracker import (  # noqa: E402
    ObserverPositionTracker,
    ObserverTrackerConfig,
)
from small_paper.pilot_runner import _observer_exit_event_row  # noqa: E402
from small_paper.observer_position_tracker import ObserverJudgmentEvent  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


class TestFwrAcceptExitPersistence(unittest.TestCase):
    def test_entry_shadow_carries_fwr_to_exit_event(self) -> None:
        cfg = ObserverTrackerConfig(
            structural_exit_policy="combined_structural_exit_v1_trailing_mfe_shadow",
            flat_weak_range_shadow_enabled=True,
            hard_stop_pct=1.2,
        )
        tracker = ObserverPositionTracker(cfg)
        trade = {
            "symbol": "1234.T",
            "profile": "test",
            "entry_time": "2026-07-17T09:10:00+09:00",
            "flat_weak_range_shadow_candidate": True,
            "flat_weak_range_shadow_block": True,
            "flat_weak_range_shadow_reason": "flat_weak_refined",
            "pretrend_shape": "E",
            "entry_type": "PBV2",
        }
        payload = {"CurrentPrice": 1000.0, "CurrentPriceTime": "2026-07-17T09:10:00+09:00"}
        tracker.register_entry(
            trade=trade, payload=payload, quality_tier="A", entry_price=1000.0
        )
        pos = tracker._positions["1234.T"]
        self.assertTrue(pos.entry_shadow.get("flat_weak_range_shadow_candidate"))
        self.assertTrue(pos.entry_shadow.get("flat_weak_range_shadow_block"))
        self.assertTrue(pos.position_id)

        ev = tracker._close(
            pos,
            reason="stop_hit",
            exit_kind="stop_hit",
            ctx={"current_price": 980.0, "unrealized_pnl_pct": -2.0},
            structural=True,
        )
        self.assertTrue(ev.context.get("flat_weak_range_shadow_candidate"))
        self.assertTrue(ev.context.get("flat_weak_range_shadow_block"))
        self.assertEqual(ev.context.get("actual_pnl_yen_100"), -2000.0)
        self.assertEqual(ev.context.get("shadow_pnl_yen_100"), 0.0)
        self.assertEqual(ev.context.get("position_id"), pos.position_id)

        row = _observer_exit_event_row(
            ev, source="test", message_index=0, profile="test"
        )
        self.assertTrue(row.get("flat_weak_range_shadow_candidate"))
        self.assertTrue(row.get("flat_weak_range_shadow_block"))
        self.assertEqual(row.get("actual_pnl_yen_100"), -2000.0)

        counters = FlatWeakRangeForwardShadowCounters()
        counters.record_accept(
            {
                "flat_weak_range_shadow_candidate": True,
                "flat_weak_range_shadow_block": True,
                "minutes_from_open": 10,
            }
        )
        counters.record_exit(row)
        summary = counters.summary_fields()
        self.assertEqual(summary["flat_weak_range_shadow_block_count"], 1)
        self.assertEqual(summary["flat_weak_range_shadow_blocked_losers"], 1)
        self.assertNotEqual(summary["flat_weak_range_shadow_actual_total_pnl_yen_100"], 0.0)


class TestCanonicalSummarySot(unittest.TestCase):
    def test_session_close_included_in_official_total(self) -> None:
        events = [
            {
                "event_type": "observer_exit",
                "symbol": "1111.T",
                "entry_price": 1000,
                "exit_price": 990,
                "pnl_pct": -1.0,
                "exit_reason": "stop_hit",
            },
            {
                "event_type": "observer_exit",
                "symbol": "2222.T",
                "entry_price": 1000,
                "exit_price": 1100,
                "pnl_pct": 10.0,
                "exit_reason": "morning_session_close",
                "session_close": True,
            },
        ]
        summary = {
            "total_pnl_yen_100": -1000.0,  # stale non-close-only
            "observer_exit_count_with_pnl": 1,
            "position_cap_mode": True,
            "observer_open_max_positions": 5,
            "peak_open_slots": 0,
        }
        enrich_summary_with_canonical(
            summary, events, max_concurrent_positions=5, watch_symbols_count=50
        )
        self.assertEqual(summary["canonical_trade_count"], 2)
        self.assertEqual(summary["canonical_total_pnl_yen_100"], 9000.0)
        self.assertEqual(summary["total_pnl_yen_100"], 9000.0)
        self.assertEqual(summary["total_pnl_yen_100_source"], "canonical_summary")
        self.assertEqual(summary["session_close_trade_count"], 1)
        self.assertEqual(summary["session_close_pnl_yen_100"], 10000.0)
        self.assertEqual(summary["non_session_close_pnl_yen_100"], -1000.0)
        self.assertEqual(summary["canonical_summary"]["max_concurrent"], 5)

    def test_peak_concurrent_position_timeline(self) -> None:
        events = [
            {
                "event_type": "accepted",
                "event_time": "t1",
                "symbol": "A.T",
                "accept_stage": "position_registered",
                "position_id": "p1",
            },
            {
                "event_type": "accepted",
                "event_time": "t2",
                "symbol": "B.T",
                "accept_stage": "position_registered",
                "position_id": "p2",
            },
            {
                "event_type": "accepted",
                "event_time": "t3",
                "symbol": "C.T",
                "accept_stage": "gate_accepted",
                "ghost_accept_reason": "entry_price_missing_or_non_positive",
            },
            {
                "event_type": "observer_exit",
                "event_time": "t4",
                "symbol": "A.T",
                "position_id": "p1",
            },
            {
                "event_type": "accepted",
                "event_time": "t5",
                "symbol": "D.T",
                "accept_stage": "position_registered",
                "position_id": "p3",
            },
        ]
        # peak open = 2 (ghost ignored)
        self.assertEqual(peak_concurrent_from_position_events(events), 2)


class TestEnrichExitFwrFields(unittest.TestCase):
    def test_enrich_exit_fields(self) -> None:
        out = enrich_exit_flat_weak_range_shadow_fields(
            {
                "flat_weak_range_shadow_candidate": True,
                "flat_weak_range_shadow_block": True,
                "flat_weak_range_shadow_reason": "flat_weak_refined",
            },
            entry_price=1000.0,
            exit_price=1050.0,
            exit_reason="trailing_mfe_exit",
        )
        self.assertTrue(out["blocked_winner"])
        self.assertEqual(out["delta_yen"], -5000.0)


if __name__ == "__main__":
    unittest.main()

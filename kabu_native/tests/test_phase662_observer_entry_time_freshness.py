"""Phase662 — observer entry time freshness fix tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ACCEPT = datetime(2026, 7, 7, 12, 58, 53, tzinfo=JST)
MARKET = datetime(2026, 7, 7, 12, 44, 14, tzinfo=JST)


def _stale_trade() -> dict:
    return {
        "symbol": "6327.T",
        "profile": "test",
        "entry_time": MARKET.isoformat(),
        "market_entry_time": MARKET.isoformat(),
        "current_price_time": MARKET.isoformat(),
        "accepted_at": ACCEPT.isoformat(),
        "accepted_event_time": ACCEPT.isoformat(),
        "exit_time": (ACCEPT + timedelta(minutes=5)).isoformat(),
        "continuation_quality_score": 0.25,
        "momentum_continuation_score": 0.0,
        "favorable_continuation": 0.15,
        "bearish_accumulation_score": 0.0,
        "price_age_sec": 877.9,
        "price_freshness_source": "liquidity_stale_trade",
    }


class Phase662ObserverEntryTimeTests(unittest.TestCase):
    def test_resolve_observer_prefers_accept_over_market(self) -> None:
        from small_paper.observer_entry_time import resolve_observer_entry_time

        ent = resolve_observer_entry_time(_stale_trade())
        self.assertEqual(ent, ACCEPT)

    def test_stale_6327_hold_30s_no_no_progress(self) -> None:
        from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW
        from small_paper.no_progress_exit import no_progress_exit_triggered
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        cfg = ObserverTrackerConfig(
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            no_progress_exit_enabled=True,
        )
        tracker = ObserverPositionTracker(cfg)
        trade = _stale_trade()
        tick_at = ACCEPT + timedelta(seconds=30)

        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = ACCEPT
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            tracker.register_entry(
                trade=trade,
                payload={},
                quality_tier="below_median",
                entry_price=5760.0,
            )

        pos = tracker._positions["6327.T"]
        self.assertEqual(pos.entry_time, ACCEPT)
        self.assertEqual(pos.market_entry_time, MARKET)
        self.assertTrue(pos.stale_trade)
        self.assertAlmostEqual(pos.market_time_age_sec or 0.0, 879.0, delta=1.0)

        hold_sec = 30.0
        self.assertFalse(
            no_progress_exit_triggered(hold_sec, pos.peak_pnl_pct, 0.0),
            "no_progress must not fire at 30s observer hold",
        )

        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = tick_at
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            events = tracker.on_tick(
                symbol="6327.T",
                trade=trade,
                payload={"CurrentPrice": 5760.0},
                current_price=5760.0,
                session_bucket="afternoon",
            )

        exit_events = [e for e in events if e.kind == "exit"]
        self.assertEqual(exit_events, [])
        hold_live = (tick_at - pos.entry_time).total_seconds()
        self.assertAlmostEqual(hold_live, 30.0, delta=0.1)

    def test_reentry_position_id_differs_and_hold_10s(self) -> None:
        from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        cfg = ObserverTrackerConfig(
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
            no_progress_exit_enabled=True,
        )
        tracker = ObserverPositionTracker(cfg)
        trade = _stale_trade()
        first_accept = ACCEPT
        second_accept = ACCEPT + timedelta(seconds=10)

        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = first_accept
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            tracker.register_entry(trade=trade, payload={}, quality_tier="A", entry_price=5760.0)
        pid1 = tracker._positions["6327.T"].position_id
        tracker._positions["6327.T"].closed = True

        trade2 = dict(trade)
        trade2["accepted_at"] = second_accept.isoformat()
        trade2["accepted_event_time"] = second_accept.isoformat()
        with patch("small_paper.observer_position_tracker.datetime") as mdt:
            mdt.now.return_value = second_accept
            mdt.combine = datetime.combine
            mdt.fromisoformat = datetime.fromisoformat
            tracker.register_entry(trade=trade2, payload={}, quality_tier="A", entry_price=5760.0)
        pid2 = tracker._positions["6327.T"].position_id
        self.assertNotEqual(pid1, pid2)

        tick_at = second_accept + timedelta(seconds=10)
        hold_sec = (tick_at - tracker._positions["6327.T"].entry_time).total_seconds()
        self.assertAlmostEqual(hold_sec, 10.0, delta=0.1)

    def test_fresh_case_hold_near_market_lag(self) -> None:
        from small_paper.observer_entry_time import market_time_age_sec, resolve_observer_entry_time

        market = datetime(2026, 7, 7, 9, 6, 1, tzinfo=JST)
        accept = market + timedelta(seconds=5)
        trade = {
            "entry_time": market.isoformat(),
            "market_entry_time": market.isoformat(),
            "accepted_at": accept.isoformat(),
        }
        observer = resolve_observer_entry_time(trade)
        age = market_time_age_sec(observer, market)
        self.assertEqual(observer, accept)
        self.assertAlmostEqual(age or 0.0, 5.0, delta=0.1)

    def test_discord_exit_detail_stale_fields(self) -> None:
        from small_paper.discord_message_builder import build_exit_detail

        detail = build_exit_detail(
            symbol="6327.T",
            entry_price=5760.0,
            exit_price=5760.0,
            pnl_pct=0.0,
            mfe_pct=0.0,
            mae_pct=0.0,
            hold_minutes=0.5,
            exit_reason="no_progress_exit",
            market_time_age_sec=879.0,
            stale_trade=True,
        )
        self.assertIn("保有時間: 0分", detail)
        self.assertIn("market_time_age_sec: 879秒", detail)
        self.assertIn("stale_trade", detail)

    def test_legacy_pre662_trade_uses_now_when_no_accept_clock(self) -> None:
        from small_paper.observer_entry_time import resolve_observer_entry_time

        market = datetime(2026, 7, 7, 9, 6, 1, tzinfo=JST)
        now = market + timedelta(seconds=2)
        trade = {"entry_time": market.isoformat()}
        ent = resolve_observer_entry_time(trade, fallback_now=now)
        self.assertEqual(ent, now)


if __name__ == "__main__":
    unittest.main()

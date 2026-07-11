"""Phase663A — PM phantom EXIT session boundary tests."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


class Phase663APhantomExitTests(unittest.TestCase):
    def _scope(self, *, kind: str = "pm") -> object:
        from small_paper.am_pm_session_policy import AmPmSessionPolicy
        from small_paper.observer_session_scope import build_observer_session_scope

        policy = (
            AmPmSessionPolicy.morning() if kind == "am" else AmPmSessionPolicy.afternoon()
        )
        return build_observer_session_scope(
            output_dir=__import__("pathlib").Path(f"live_session_test_{kind}"),
            trade_date=date(2026, 7, 8),
            am_pm_policy=policy,
        )

    def _trade(self, *, accept_at: datetime, symbol: str = "6327.T") -> dict:
        return {
            "symbol": symbol,
            "profile": "test",
            "entry_time": accept_at.isoformat(),
            "market_entry_time": (accept_at.replace(hour=12, minute=0)).isoformat(),
            "accepted_at": accept_at.isoformat(),
            "accepted_event_time": accept_at.isoformat(),
            "exit_time": (accept_at.replace(hour=15)).isoformat(),
            "continuation_quality_score": 0.5,
            "momentum_continuation_score": 0.1,
            "favorable_continuation": 0.5,
            "bearish_accumulation_score": 0.0,
        }

    def test_bind_session_clears_am_carryover(self) -> None:
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        tracker = ObserverPositionTracker(ObserverTrackerConfig())
        am_scope = self._scope(kind="am")
        am_accept = datetime(2026, 7, 8, 9, 10, 0, tzinfo=JST)
        tracker.bind_session(am_scope)
        tracker.register_entry(
            trade=self._trade(accept_at=am_accept),
            payload={},
            quality_tier="A",
            entry_price=1000.0,
        )
        self.assertTrue(tracker.has_open("6327.T"))

        pm_scope = self._scope(kind="pm")
        tracker.bind_session(pm_scope)
        self.assertFalse(tracker.has_open("6327.T"))
        self.assertEqual(tracker.session_id, pm_scope.session_id)

    def test_warmup_register_blocked_before_allowed_entry(self) -> None:
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        tracker = ObserverPositionTracker(ObserverTrackerConfig())
        tracker.bind_session(self._scope())
        early = datetime(2026, 7, 8, 12, 20, 0, tzinfo=JST)
        tracker.register_entry(
            trade=self._trade(accept_at=early),
            payload={},
            quality_tier="A",
            entry_price=1000.0,
        )
        self.assertFalse(tracker.has_open("6327.T"))

    def test_pm_register_and_exit_carries_session_id(self) -> None:
        from small_paper.observer_position_tracker import OBSERVER_EXIT
        from small_paper.observer_position_tracker import ObserverPositionTracker, ObserverTrackerConfig

        tracker = ObserverPositionTracker(ObserverTrackerConfig())
        scope = self._scope()
        tracker.bind_session(scope)
        accept = datetime(2026, 7, 8, 13, 0, 19, tzinfo=JST)
        trade = self._trade(accept_at=accept)
        tracker.register_entry(trade=trade, payload={}, quality_tier="A", entry_price=1000.0)
        pos = tracker._positions["6327.T"]
        self.assertEqual(pos.session_id, scope.session_id)

        events = tracker.on_tick(
            symbol="6327.T",
            trade=trade,
            payload={"CurrentPrice": 990.0},
            current_price=990.0,
            session_bucket="afternoon",
        )
        # may or may not exit on first tick; session_id present on any exit
        for ev in events:
            if ev.kind == OBSERVER_EXIT:
                self.assertEqual(ev.context.get("session_id"), scope.session_id)

    def test_dispatch_skips_foreign_session_exit(self) -> None:
        from small_paper.observer_position_tracker import OBSERVER_EXIT, ObserverJudgmentEvent
        from small_paper.pilot_runner import _dispatch_observer_events

        discord = MagicMock()
        discord.active = True
        ev = ObserverJudgmentEvent(
            kind=OBSERVER_EXIT,
            symbol="6327.T",
            context={
                "is_structural_exit": True,
                "session_id": "20260708_am_live_session_081852",
                "symbol": "6327.T",
            },
        )
        _dispatch_observer_events(
            [ev],
            discord=discord,
            observer_session_id="20260708_pm_live_session_122537",
        )
        discord.notify_exit.assert_not_called()

    def test_audit_finds_am_clock_phantom(self) -> None:
        from research.phase663a_pm_phantom_exit_audit import audit_pm_phantom_exits

        pm_events = [
            {
                "event_type": "observer_exit",
                "symbol": "6327.T",
                "event_time": "2026-07-08T13:05:00+09:00",
                "exit_reason": "no_progress_exit",
                "observer_entry_time": "2026-07-08T09:11:57+09:00",
            }
        ]
        am_events = [
            {
                "event_type": "accepted",
                "symbol": "6327.T",
                "event_time": "2026-07-08T09:11:57+09:00",
            }
        ]
        phantoms = audit_pm_phantom_exits(pm_events, am_events=am_events)
        self.assertEqual(len(phantoms), 1)
        self.assertEqual(phantoms[0].entry_session, "am")


if __name__ == "__main__":
    unittest.main()

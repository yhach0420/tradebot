"""Unit tests for E1_X5_G1 confirmation guard (offline research)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession, THRESHOLD
from small_paper.e1_x5_g1_confirmation_guard import (
    E1X5GuardSession,
    GuardVariant,
    is_independent_push,
)

JST = ZoneInfo("Asia/Tokyo")
T0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=JST)


def test_same_snapshot_not_independent():
    ok, reason = is_independent_push(
        arm_sequence=10,
        arm_time=T0,
        seq=10,
        ts=T0 + timedelta(seconds=1),
        bid=100.0,
        ask=100.1,
        last_seen_sequence=10,
    )
    assert not ok
    assert reason == "SEQ_NOT_AFTER_ARM"


def test_confirmation_seq_must_increase():
    ok, _ = is_independent_push(
        arm_sequence=10,
        arm_time=T0,
        seq=11,
        ts=T0 + timedelta(seconds=1),
        bid=100.0,
        ask=100.1,
        last_seen_sequence=10,
    )
    assert ok


def test_duplicate_inversion_rejected():
    ok, reason = is_independent_push(
        arm_sequence=10,
        arm_time=T0,
        seq=12,
        ts=T0 + timedelta(seconds=1),
        bid=100.0,
        ask=100.1,
        last_seen_sequence=12,
    )
    assert not ok
    assert reason == "DUPLICATE_OR_INVERSION"


def test_c2_blocks_lower_bid():
    s = E1X5GuardSession(enabled=True, variant=GuardVariant.C2_NO_LOWER_BID)
    s.try_entry(
        symbol="1000.T",
        ts=T0,
        bid=100.0,
        ask=100.05,
        score=THRESHOLD + 0.1,
        spread_bps=5.0,
        event_sequence=1,
        day="20260723",
    )
    assert "1000.T" in s.pending
    r = s.confirm_on_independent_push(
        symbol="1000.T",
        ts=T0 + timedelta(seconds=1),
        bid=99.0,
        ask=99.05,
        sequence=2,
        observe_kind="SCORE",
        score=THRESHOLD + 0.1,
        spread_bps=5.0,
        day="20260723",
    )
    assert r == "BID_LOWER_THAN_ARM"
    assert "1000.T" not in s.positions


def test_c3_enters_after_rebound_at_confirm_ask():
    s = E1X5GuardSession(enabled=True, variant=GuardVariant.C3_BID_REBOUND)
    s.try_entry(
        symbol="1001.T",
        ts=T0,
        bid=100.0,
        ask=100.05,
        score=THRESHOLD + 0.1,
        spread_bps=4.0,
        event_sequence=1,
        day="20260723",
    )
    s.confirm_on_independent_push(
        symbol="1001.T",
        ts=T0 + timedelta(seconds=1),
        bid=99.5,
        ask=99.55,
        sequence=2,
        observe_kind="SCORE",
        score=THRESHOLD + 0.1,
        spread_bps=4.0,
        day="20260723",
    )
    assert "1001.T" in s.pending
    r = s.confirm_on_independent_push(
        symbol="1001.T",
        ts=T0 + timedelta(seconds=2),
        bid=99.8,
        ask=99.85,
        sequence=3,
        observe_kind="SCORE",
        score=THRESHOLD + 0.1,
        spread_bps=4.0,
        day="20260723",
    )
    assert r is None
    assert "1001.T" in s.positions
    assert abs(s.positions["1001.T"].entry_ask - 99.85) < 1e-9


def test_cap5_and_same_symbol():
    s = E1X5GuardSession(enabled=True, variant=GuardVariant.BASE)
    for i in range(5):
        s.try_entry(
            symbol=f"{2000+i}.T",
            ts=T0,
            bid=10.0,
            ask=10.01,
            score=THRESHOLD + 0.1,
            spread_bps=1.0,
            event_sequence=i + 1,
            day="20260723",
        )
    assert s.try_entry(
        symbol="2999.T",
        ts=T0,
        bid=10.0,
        ask=10.01,
        score=THRESHOLD + 0.1,
        spread_bps=1.0,
        event_sequence=99,
        day="20260723",
    ) == "CAP5_BLOCKED"
    assert s.try_entry(
        symbol="2000.T",
        ts=T0 + timedelta(seconds=1),
        bid=10.0,
        ask=10.01,
        score=THRESHOLD + 0.1,
        spread_bps=1.0,
        event_sequence=100,
        day="20260723",
    ) == "SAME_SYMBOL_OPEN"


def test_state_rearm_requires_false_then_true():
    s = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=True)
    s.disarmed_after_stop.add("3000.T")
    assert (
        s.try_entry(
            symbol="3000.T",
            ts=T0,
            bid=10.0,
            ask=10.01,
            score=THRESHOLD + 0.1,
            spread_bps=1.0,
            event_sequence=1,
            day="20260723",
        )
        == "DISARMED_AFTER_STOP"
    )
    s.note_predicate_observation("3000.T", score=0.1, spread_bps=1.0, valid_eval=True)
    assert "3000.T" in s.saw_false_since_disarm
    assert (
        s.try_entry(
            symbol="3000.T",
            ts=T0 + timedelta(seconds=1),
            bid=10.0,
            ask=10.01,
            score=THRESHOLD + 0.1,
            spread_bps=1.0,
            event_sequence=2,
            day="20260723",
        )
        == "PENDING_CONFIRMATION"
    )


def test_no_evaluation_does_not_rearm():
    s = E1X5GuardSession(enabled=True, variant=GuardVariant.C1_NEXT_PUSH_HOLD, state_rearm=True)
    s.disarmed_after_stop.add("3001.T")
    s.note_predicate_observation("3001.T", score=None, spread_bps=None, valid_eval=False)
    assert "3001.T" not in s.saw_false_since_disarm


def test_base_session_unchanged_immediate_entry():
    base = E1X5ForwardShadowSession(enabled=True)
    g = E1X5GuardSession(enabled=True, variant=GuardVariant.BASE)
    for sess in (base, g):
        sess.try_entry(
            symbol="4000.T",
            ts=T0,
            bid=10.0,
            ask=10.01,
            score=THRESHOLD + 0.1,
            spread_bps=1.0,
            event_sequence=1,
            day="20260723",
        )
    assert "4000.T" in base.positions and "4000.T" in g.positions

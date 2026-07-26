"""Phase676 recovery market-price selection tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.recovery_market_price import (
    SOURCE_ASK,
    SOURCE_BID,
    SOURCE_CURRENT,
    SOURCE_ENTRY,
    SOURCE_MID,
    PriceCandidate,
    select_recovery_price,
)
from small_paper.ws_freeze_recovery import (
    apply_orphan_recovery_to_events,
    build_recovery_forced_close_exit,
    find_orphan_accepted,
)

JST = ZoneInfo("Asia/Tokyo")


def _ts(h=11, m=20, s=0):
    return datetime(2026, 7, 21, h, m, s, tzinfo=JST)


def test_1_bid_preferred():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [
        PriceCandidate(timestamp=_ts(11, 24), symbol="1000.T", bid=100.0, ask=101.0, current_price=100.5),
    ]
    d = select_recovery_price(symbol="1000.T", entry_price=99.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_BID
    assert d.recovery_price == 100.0


def test_2_current_price():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 24), symbol="1000.T", current_price=105.0)]
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_CURRENT
    assert d.recovery_price == 105.0
    assert d.pnl_yen_100 == 500.0


def test_3_board_mid():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 24), symbol="1000.T", board_mid=102.0)]
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_MID
    assert d.recovery_price == 102.0


def test_4_ask_with_warning():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 24), symbol="1000.T", ask=103.0)]
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_ASK
    assert "ASK_USED_FOR_LONG_CLOSE_OPTIMISTIC" in d.warning


def test_5_entry_fallback():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=[])
    assert d.recovery_price_source == SOURCE_ENTRY
    assert d.fallback_used is True
    assert d.pnl_yen_100 == 0.0


def test_6_reject_future_price():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 26), symbol="1000.T", current_price=200.0)]
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_ENTRY
    assert d.future_leak_check == "PASS"


def test_7_reject_before_entry():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 5), symbol="1000.T", current_price=90.0)]
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price_source == SOURCE_ENTRY


def test_8_stale_warning():
    entry = _ts(11, 10)
    fc = _ts(11, 25)
    cands = [PriceCandidate(timestamp=_ts(11, 20), symbol="1000.T", current_price=101.0)]  # age 300s
    d = select_recovery_price(symbol="1000.T", entry_price=100.0, entry_time=entry, force_close=fc, candidates=cands)
    assert d.recovery_price == 101.0
    assert "STALE_LAST_MARKET_PRICE" in d.warning


def test_9_idempotent_no_double_exit():
    events = [
        {"event_type": "accepted", "symbol": "1000.T", "position_id": "p1", "entry_price": 100, "entry_time": _ts(11, 10).isoformat()},
        {
            "event_type": "observer_exit",
            "exit_reason": "recovery_forced_close",
            "symbol": "1000.T",
            "position_id": "p1",
            "entry_price": 100,
            "exit_price": 100,
            "pnl_pct": 0,
        },
    ]
    before = len(events)
    r = apply_orphan_recovery_to_events(events, recovery_note="test", closed_at=_ts(11, 25).isoformat())
    assert r.orphan_forced_close_count == 0
    assert len(events) == before
    assert sum(1 for e in events if e.get("exit_reason") == "recovery_forced_close") == 1


def test_10_skip_already_normal_exit():
    events = [
        {"event_type": "accepted", "symbol": "1000.T", "position_id": "p1", "entry_price": 100, "entry_time": _ts(11, 10).isoformat()},
        {
            "event_type": "observer_exit",
            "exit_reason": "stop_hit",
            "symbol": "1000.T",
            "position_id": "p1",
            "entry_price": 100,
            "exit_price": 95,
            "pnl_pct": -5,
        },
    ]
    assert find_orphan_accepted(events) == []
    r = apply_orphan_recovery_to_events(events, recovery_note="test", closed_at=_ts(11, 25).isoformat())
    assert r.orphan_forced_close_count == 0


def test_build_exit_uses_decision(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    # one current price before force close
    row = {
        "event_type": "rejected",
        "symbol": "1000.T",
        "event_time": _ts(11, 24).isoformat(),
        "current_price": 110.0,
        "current_price_time": _ts(11, 24).isoformat(),
    }
    events_path.write_text(__import__("json").dumps(row) + "\n", encoding="utf-8")
    accepted = {
        "event_type": "accepted",
        "symbol": "1000.T",
        "position_id": "p1",
        "entry_price": 100.0,
        "entry_time": _ts(11, 10).isoformat(),
    }
    exit_ev = build_recovery_forced_close_exit(
        accepted,
        closed_at=_ts(12, 0).isoformat(),
        recovery_note="unit",
        force_close_at=_ts(11, 25).isoformat(),
        events_path=events_path,
    )
    assert exit_ev["exit_price"] == 110.0
    assert exit_ev["pnl_yen_100"] == 1000.0
    assert exit_ev["recovery_price_source"] == SOURCE_CURRENT

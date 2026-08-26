"""P2-4B clock/coverage unit checks. No Capture. No PnL."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research.trailing10_dynamic_anchor_p2_4a import EVALUABLE, NOT_EVALUABLE, SESSION_INVALID
from research.trailing10_dynamic_anchor_p2_4a.contract import evaluate_trail
from research.trailing10_full_history_p2_4b.binding import verify_frozen_spec
from research.trailing10_full_history_p2_4b.clock import IncrementalTrail10
from research.trailing10_full_history_p2_4b.coverage import PRIOR_EDGE, classify_fixed_trade

JST = ZoneInfo("Asia/Tokyo")
DAY = "20990106"


def _ep(h: int, m: int, s: int = 0) -> float:
    return datetime(2099, 1, 6, h, m, s, tzinfo=JST).timestamp()


def test_frozen_sha_match():
    b = verify_frozen_spec()
    assert b["pass"], b


def test_session_invalid_not_false():
    g = _ep(9, 5, 0)
    r = evaluate_trail(symbol="A", g=g, day=DAY, events=[])
    assert r["status"] == NOT_EVALUABLE
    assert r["reason"] == SESSION_INVALID
    assert r["trail10_state"] is None


def test_clock_no_ticks_no_anchor():
    clock = IncrementalTrail10(day=DAY, universe=["A"])
    g = _ep(10, 10, 0)
    idx = None
    for i, (_sess, gt) in enumerate(clock.grids_spec):
        if abs(gt.timestamp() - g) < 1e-6:
            idx = i
            break
    assert idx is not None
    clock.grid_i = idx
    clock.evaluate_grids_until(g + 0.001)
    assert clock.anchors == []
    due = clock.due_anchors(g + 0.001)
    assert due == []
    assert clock.ne_created_edge == 0


def test_coverage_prior_edge():
    g = 1000.0
    out = classify_fixed_trade(
        symbol="A",
        session="AM",
        signal_t=1010.0,
        anchors=[{"symbol": "A", "session": "AM", "g": g}],
        evals=[{"symbol": "A", "session": "AM", "g": g, "status": EVALUABLE, "trail10_state": True}],
    )
    assert out["has_prior_trail10_edge"] is True
    assert out["state_at_fixed_signal"] == PRIOR_EDGE

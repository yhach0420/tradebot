"""Occupancy release + Frozen AM 11:30 / PM 15:00 session-close regressions."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from small_paper.v1r_live_dual_lane import (
    V1RLiveDualLane,
    canonical_symbol_key,
    ensure_dual_lane,
    reset_dual_lane_for_tests,
)
from small_paper.v1r_native_entry_live import (
    PendingOrder,
    V1RNativeEntryLive,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"


def _snap(t: float, bid: float = 100.0, ask: float = 101.0, bq: float = 200.0, aq: float = 200.0):
    return {
        "event_time": t,
        "CurrentPriceTime": t,
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "CurrentPrice": (bid + ask) / 2.0,
        "board_age_sec": 0.0,
        "SpecialQuote": False,
        "imbalance": (bq - aq) / (bq + aq),
    }


def _boot(tmp_path: Path) -> tuple[V1RNativeEntryLive, V1RLiveDualLane]:
    reset_native_entry_for_tests()
    reset_dual_lane_for_tests()
    eng = V1RNativeEntryLive(
        universe=["6098", "285A", "5803", "5985", "8050"],
        score_fn=lambda _f: 0.0,
        model_ser={},
        trace_dir=tmp_path,
        trading_date=DAY,
    )
    set_native_entry(eng)
    dual = ensure_dual_lane(trace_dir=tmp_path)
    assert dual is not None
    return eng, dual


def _fill_native(
    eng: V1RNativeEntryLive,
    dual: V1RLiveDualLane,
    *,
    symbol: str,
    t0: float,
    session: str,
    bid: float = 100.0,
) -> None:
    key = canonical_symbol_key(symbol)
    po = PendingOrder(
        symbol=key,
        signal_time=t0 - 0.5,
        limit_price=bid,
        score=1.0,
        rank=1,
        anchor="09:05",
        session=session,
        date=DAY,
    )
    eng.pending[key] = po
    eng.boards[key] = [
        {"t": t0, "bid": bid, "ask": bid + 1.0, "bid_qty": 200.0, "ask_qty": 200.0, "special": False, "fresh_sec": 0.0}
    ]
    eng._promote_fill(po, {"fill_price": bid, "fill_t": t0, "filled": True})


def test_canonical_6098_dot_t_same_key():
    assert canonical_symbol_key("6098") == canonical_symbol_key("6098.T") == "6098"


def test_fill_plus_one_primary_exit_minus_one_control_unchanged(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    _fill_native(eng, dual, symbol="6098", t0=t0, session="AM")
    assert eng.open_n == 1
    assert eng.pending_n == 0
    assert eng.exposure() == 1
    assert dual.open_n("primary") == 1
    assert dual.open_n("control") == 1
    inv = next(e for e in eng.events if e.get("event") == "FILL" and e.get("kind") == "V1R_OCCUPANCY_INVARIANT")
    assert inv["ok"] is True
    assert inv["native_open"] == 1

    native_before_ctrl = eng.exposure()
    # Horizon EXIT at 600s — Primary then Control.
    for off in (1.0, 600.0):
        dual.on_tick(symbol="6098.T", payload=_snap(t0 + off, bid=100.0), event_t=t0 + off)
    assert dual.primary["6098"].closed
    assert dual.control["6098"].closed
    assert dual.open_n("primary") == 0
    assert dual.open_n("control") == 0
    assert eng.open_n == 0
    assert eng.exposure() == 0
    releases = [e for e in eng.events if e.get("kind") == "V1R_NATIVE_PRIMARY_EXIT_RELEASE"]
    assert len(releases) == 1
    assert releases[0]["symbol"] == "6098"
    assert releases[0]["native_open_before"] == 1
    assert releases[0]["native_open_after"] == 0
    assert releases[0]["duplicate"] is False
    # Control EXIT must not change native occupancy (already 0 after Primary).
    assert eng.exposure() == native_before_ctrl - 1


def test_note_primary_exit_idempotent_and_dot_t(tmp_path: Path):
    eng, _dual = _boot(tmp_path)
    eng.open_symbols.add("6098")
    r1 = eng.note_primary_exit("6098.T", exit_time=1.0, reason="CONT_EXIT_600")
    assert r1["symbol"] == "6098"
    assert r1["native_open_before"] == 1
    assert r1["native_open_after"] == 0
    assert r1["duplicate"] is False
    r2 = eng.note_primary_exit("6098", exit_time=1.0, reason="CONT_EXIT_600")
    assert r2["duplicate"] is True
    assert r2["native_open_before"] == 0
    assert r2["native_open_after"] == 0
    assert eng.open_n == 0


def test_expire_pending_minus_one(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    po = PendingOrder(
        symbol="6098",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=1,
        anchor="09:05",
        session="AM",
        date=DAY,
    )
    eng.pending["6098"] = po
    # Board with ask never crossing limit → EXPIRE after wait.
    eng.boards["6098"] = [
        {
            "t": t0 + 0.1,
            "bid": 99.0,
            "ask": 200.0,
            "bid_qty": 200.0,
            "ask_qty": 200.0,
            "special": False,
            "fresh_sec": 0.0,
        }
    ]
    assert eng.pending_n == 1
    assert eng.exposure() == 1
    done = eng.on_tick_fill_check(event_t=t0 + WAIT_SEC + 0.01)
    assert any(d.get("kind") == "V1R_EXPIRED" for d in done)
    assert eng.pending_n == 0
    assert eng.open_n == 0
    assert eng.exposure() == 0
    assert dual.open_n("primary") == 0
    inv = [e for e in eng.events if e.get("event") == "EXPIRED" and "OCCUPANCY" in str(e.get("kind"))]
    assert inv and inv[-1]["ok"] is True


def test_native_open_cap_with_primary_zero_is_fail(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    for i, s in enumerate(["6098", "285A", "5803", "5985", "8050"]):
        eng.open_symbols.add(s)
    assert eng.open_n == POSITION_CAP
    assert dual.open_n("primary") == 0
    rec = eng.check_occupancy_invariant(dual=dual, event="AUDIT")
    assert rec["ok"] is False
    assert rec["cap_desync"] is True
    assert dual.fail_closed is True
    assert dual.fail_reason == "OCCUPANCY_INVARIANT"


def test_control_exit_does_not_release_native(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    t0 = datetime(2026, 8, 12, 9, 5, tzinfo=JST).timestamp()
    _fill_native(eng, dual, symbol="6098", t0=t0, session="AM")
    pos = dual.control["6098"]
    dual._close(
        pos,
        {
            "exit": True,
            "reason": "FIXED600",
            "exit_time": t0 + 600,
            "exit_price": 100.0,
            "exit_off": 600.0,
            "triggered_guard": False,
            "extended": False,
        },
        _snap(t0 + 600),
    )
    assert dual.open_n("control") == 0
    assert dual.open_n("primary") == 1
    assert eng.open_n == 1
    assert not any(e.get("kind") == "V1R_NATIVE_PRIMARY_EXIT_RELEASE" for e in eng.events)
    inv = [e for e in eng.events if e.get("event") == "CONTROL_ACTUAL_EXIT"]
    assert inv and inv[-1]["ok"] is True
    assert inv[-1]["native_open"] == 1
    assert inv[-1]["primary_open"] == 1


def _session_case(tmp_path: Path, session: str):
    eng, dual = _boot(tmp_path)
    se = session_end_epoch(DAY, session)
    t0 = se - 600.0  # 10 minutes before Frozen close — horizon not reached
    _fill_native(eng, dual, symbol="6098", t0=t0, session=session, bid=50550.0)
    dual.on_tick(symbol="6098", payload=_snap(t0 + 1.0, bid=50550.0), event_t=t0 + 1.0)
    dual.on_tick(symbol="6098.T", payload=_snap(t0 + 30.0, bid=50600.0), event_t=t0 + 30.0)
    assert dual.open_n("primary") == 1
    assert dual.open_n("control") == 1
    assert eng.open_n == 1
    # No future quotes: close at Frozen sess_end using last stored Buy1.
    exits = dual.close_open_at_session_end(event_t=se, session=session)
    assert len(exits) == 2
    assert {e["lane"] for e in exits} == {"primary", "control"}
    assert all(e["reason"] == "SESSION_CLOSE" for e in exits)
    assert all(e["exit_time"] <= se + 1e-9 for e in exits)
    assert all(e["exit_time"] < se + 1.0 for e in exits)
    # Last stored bid is 50600 at t0+30, not a synthetic 11:30/15:00 quote.
    assert all(abs(float(e["exit_price"]) - 50600.0) < 1e-9 for e in exits)
    assert dual.open_n("primary") == 0
    assert dual.open_n("control") == 0
    assert eng.open_n == 0
    assert eng.pending_n == 0
    p = dual.primary["6098"]
    c = dual.control["6098"]
    assert p.closed and c.closed
    assert p.exit_reason == "SESSION_CLOSE"
    assert c.exit_reason == "SESSION_CLOSE"
    releases = [e for e in eng.events if e.get("kind") == "V1R_NATIVE_PRIMARY_EXIT_RELEASE"]
    assert len(releases) == 1
    return exits, p, c, se


def test_case_a_am_session_close(tmp_path: Path):
    exits, p, c, se = _session_case(tmp_path, "AM")
    assert datetime.fromtimestamp(se, JST).hour == 11
    assert datetime.fromtimestamp(se, JST).minute == 30
    assert p.exit_time < se + 1e-9
    assert c.exit_time < se + 1e-9


def test_case_b_pm_session_close(tmp_path: Path):
    exits, p, c, se = _session_case(tmp_path, "PM")
    assert datetime.fromtimestamp(se, JST).hour == 15
    assert datetime.fromtimestamp(se, JST).minute == 0
    assert p.exit_time < se + 1e-9
    assert c.exit_time < se + 1e-9


def test_session_close_does_not_close_other_session(tmp_path: Path):
    eng, dual = _boot(tmp_path)
    am_end = session_end_epoch(DAY, "AM")
    t0 = am_end - 120.0
    _fill_native(eng, dual, symbol="6098", t0=t0, session="AM")
    pm_t0 = session_end_epoch(DAY, "PM") - 120.0
    _fill_native(eng, dual, symbol="285A", t0=pm_t0, session="PM", bid=200.0)
    dual.close_open_at_session_end(event_t=am_end, session="AM")
    assert dual.primary["6098"].closed
    assert not dual.primary["285A"].closed
    assert "285A" in eng.open_symbols
    assert "6098" not in eng.open_symbols

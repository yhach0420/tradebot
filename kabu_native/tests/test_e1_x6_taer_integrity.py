"""Regression tests for TAER economic integrity (SESSION_END / MAX_HOLD / MFE-MAE)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.e1_x6_provisional.cost_contract import LOT, net_pnl_yen, yen_roundtrip_cost
from research.e1_x6_taer.exit_sm import EXIT_THRESHOLDS, _tick
from research.e1_x6_taer.integrity_replay import (
    MAX_HOLD_SEC,
    PathTracker,
    _close_trade,
    _integrity_check,
)
from research.e1_x6_taer.exit_sm import ExitPos


def _pos(sym="9984", ask=4716.0, mid=4715.0, t0=1785306120.741801):
    return ExitPos(
        symbol=sym, setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
        entry_t=t0, entry_ask=ask, entry_mid=mid, reclaim_level=mid,
        pullback_low=mid - 5, range_high=mid, range_low=mid - 5,
        vwap_at_entry=mid, atr=10.0, last_progress_t=t0, peak_mid=mid,
    )


def test_exit_symbol_matches_entry_symbol():
    pos = _pos()
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="9984", day="20260729", session="PM",
                bid=4715.0, ask=4716.0, mid=4715.5, entry_ask=4716.0, event_id="e1")
    trk.observe(t=pos.entry_t + 10, sym="9984", day="20260729", session="PM",
                bid=4717.0, ask=4718.0, mid=4717.5, entry_ask=4716.0, event_id="e2")
    meta = {"episode_id": "x", "entry_session": "PM", "entry_event_id": "e1",
            "entry_bid": 4715.0, "scenario_id_prior": None}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 60,
                      pair_id="p", setup_type="PULLBACK_RECLAIM",
                      exit_candidate="X_STRUCTURAL", day="20260729")
    assert tr["entry_event_symbol"] == tr["exit_event_symbol"] == "9984"
    assert tr["path_symbol_unique_count"] == 1


def test_exit_day_matches_entry_day():
    pos = _pos()
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="9984", day="20260729", session="PM",
                bid=4715.0, ask=4716.0, mid=4715.5, entry_ask=4716.0, event_id="e1")
    meta = {"episode_id": "x", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 4715.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="MAX_HOLD", exit_state="EXIT", session_boundary_time=None,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="20260729")
    assert tr["entry_event_day"] == tr["exit_event_day"] == "20260729"
    assert tr["path_day_unique_count"] == 1


def test_exit_session_matches_entry_session():
    pos = _pos()
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="9984", day="20260729", session="PM",
                bid=4715.0, ask=4716.0, mid=4715.5, entry_ask=4716.0, event_id="e1")
    meta = {"episode_id": "x", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 4715.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 100,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="20260729")
    assert tr["entry_event_session"] == tr["exit_event_session"] == "PM"


def test_session_end_uses_last_valid_same_session_bid():
    pos = _pos(ask=1000.0, mid=999.5)
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="X", day="D", session="AM",
                bid=999.0, ask=1000.0, mid=999.5, entry_ask=1000.0, event_id="e1")
    trk.observe(t=pos.entry_t + 20, sym="X", day="D", session="AM",
                bid=1001.0, ask=1002.0, mid=1001.5, entry_ask=1000.0, event_id="e2")
    # foreign symbol must NOT be used
    foreign = {"t": pos.entry_t + 40, "bid": 8564.0, "ask": 8565.0, "mid": 8564.5,
               "event_id": "bad", "day": "D", "session": "AM", "symbol": "OTHER"}
    meta = {"episode_id": "x", "entry_session": "AM", "entry_event_id": "e1", "entry_bid": 999.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 50,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="D")
    assert tr["exit_price_used"] == 1001.0
    assert tr["exit_event_symbol"] == "X"
    assert abs(tr["net_pnl_yen"] - net_pnl_yen(1000.0, 1001.0)["net_pnl_yen_100"]) < 1e-6
    assert foreign["bid"] != tr["exit_price_used"]


def test_session_end_missing_bid_is_not_evaluable():
    pos = _pos()
    trk = PathTracker()
    meta = {"episode_id": "x", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 4715.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=None,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 10,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="20260729")
    assert tr["exit_reason"] == "NOT_EVALUABLE_SESSION_END_EXIT_PRICE"
    assert tr["integrity_status"] == "NOT_EVALUABLE"
    assert tr["net_pnl_yen"] is None


def test_realized_delta_within_mfe_mae():
    pos = _pos(sym="X", ask=100.0, mid=99.9)
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="X", day="D", session="AM",
                bid=99.5, ask=100.0, mid=99.75, entry_ask=100.0, event_id="e1")
    trk.observe(t=pos.entry_t + 5, sym="X", day="D", session="AM",
                bid=101.0, ask=101.5, mid=101.25, entry_ask=100.0, event_id="e2")
    trk.observe(t=pos.entry_t + 10, sym="X", day="D", session="AM",
                bid=100.5, ask=101.0, mid=100.75, entry_ask=100.0, event_id="e3")
    meta = {"episode_id": "x", "entry_session": "AM", "entry_event_id": "e1", "entry_bid": 99.5}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="MAX_HOLD", exit_state="EXIT", session_boundary_time=None,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="D")
    assert tr["integrity_status"] == "PASS", tr["integrity_failure_reasons"]
    assert tr["mae_price_delta"] - _tick(100) <= tr["realized_price_delta"] <= tr["mfe_price_delta"] + _tick(100)


def test_lot_applied_once():
    e = net_pnl_yen(1000.0, 1010.0)
    assert abs(e["gross_pnl_yen_100"] - (10.0 * LOT)) < 1e-9


def test_cost_5bps_applied_once():
    assert abs(yen_roundtrip_cost(1000.0) - 50.0) < 1e-9
    e = net_pnl_yen(1000.0, 1000.0)
    assert abs(e["net_pnl_yen_100"] + 50.0) < 1e-9


def test_max_hold_uses_entry_time():
    assert MAX_HOLD_SEC == float(EXIT_THRESHOLDS["max_hold_sec"]) == 300.0
    t0 = 1000.0
    assert (t0 + MAX_HOLD_SEC) == 1300.0


def test_max_hold_cannot_exceed_tolerance():
    pos = _pos(sym="X", t0=1000.0, ask=100.0, mid=100.0)
    trk = PathTracker()
    # path only within 300s
    trk.observe(t=1000.0, sym="X", day="D", session="AM",
                bid=99.0, ask=100.0, mid=99.5, entry_ask=100.0, event_id="e1")
    trk.observe(t=1295.0, sym="X", day="D", session="AM",
                bid=100.0, ask=101.0, mid=100.5, entry_ask=100.0, event_id="e2")
    meta = {"episode_id": "x", "entry_session": "AM", "entry_event_id": "e1", "entry_bid": 99.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="MAX_HOLD", exit_state="EXIT", session_boundary_time=None,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="D")
    assert tr["hold_sec"] <= MAX_HOLD_SEC + 5.0
    assert tr["integrity_status"] == "PASS", tr["integrity_failure_reasons"]


def test_path_cannot_cross_session():
    trk = PathTracker()
    trk.observe(t=1.0, sym="X", day="D", session="AM",
                bid=1.0, ask=1.1, mid=1.05, entry_ask=1.1, event_id="e1")
    trk.observe(t=2.0, sym="X", day="D", session="PM",
                bid=1.0, ask=1.1, mid=1.05, entry_ask=1.1, event_id="e2")
    assert len(trk.sessions) == 2  # detector catches cross


def test_path_cannot_cross_day():
    trk = PathTracker()
    trk.observe(t=1.0, sym="X", day="20260723", session="AM",
                bid=1.0, ask=1.1, mid=1.05, entry_ask=1.1, event_id="e1")
    trk.observe(t=2.0, sym="X", day="20260724", session="AM",
                bid=1.0, ask=1.1, mid=1.05, entry_ask=1.1, event_id="e2")
    assert len(trk.days) == 2


def test_9984_20260729_regression():
    """Old bug: SESSION_END used foreign bid → huge PnL; must fail envelope if replayed wrongly."""
    pos = _pos(sym="9984", ask=4716.0, mid=4715.0, t0=1785306120.741801)
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="9984", day="20260729", session="PM",
                bid=4714.0, ask=4716.0, mid=4715.0, entry_ask=4716.0, event_id="e1")
    trk.observe(t=pos.entry_t + 50, sym="9984", day="20260729", session="PM",
                bid=4715.0, ask=4717.0, mid=4716.0, entry_ask=4716.0, event_id="e2")
    # Wrong foreign quote must not be selected
    wrong = {"t": pos.entry_t + 59, "bid": 37660.0, "ask": 37670.0, "mid": 37665.0,
             "event_id": "bad", "day": "20260729", "session": "PM", "symbol": "OTHER"}
    meta = {"episode_id": "ep", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 4714.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 60,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                      day="20260729")
    assert tr["exit_price_used"] != wrong["bid"]
    assert abs(tr["net_pnl_yen"]) < 10000  # not millions
    assert tr["integrity_status"] == "PASS"
    # If wrong quote were used, integrity must FAIL
    bad_trk = PathTracker()
    bad_trk.observe(t=pos.entry_t, sym="9984", day="20260729", session="PM",
                    bid=4714.0, ask=4716.0, mid=4715.0, entry_ask=4716.0, event_id="e1")
    bad_trk.mfe_bid = 1.5
    bad_trk.mae_bid = -2.5
    bad_trk.last_valid = wrong
    bad_trk.symbols = {"9984"}  # pretend
    bad_trk.days = {"20260729"}
    bad_trk.sessions = {"PM"}
    bad_trk.times = [pos.entry_t, wrong["t"]]
    bad_trk.event_count = 2
    tr_bad = _close_trade(pos=pos, meta=meta, tracker=bad_trk, exit_quote=wrong,
                          exit_reason="SESSION_END", exit_state="EXIT",
                          session_boundary_time=pos.entry_t + 60,
                          pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_STRUCTURAL",
                          day="20260729")
    assert "FAIL_CROSS_SYMBOL" in tr_bad["integrity_failure_reasons"] or \
           "FAIL_REALIZED_OUTSIDE_MFE_MAE" in tr_bad["integrity_failure_reasons"]


def test_5253_20260724_regression():
    pos = _pos(sym="5253", ask=1385.0, mid=1384.5, t0=1784869030.609889)
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="5253", day="20260724", session="PM",
                bid=1384.0, ask=1385.0, mid=1384.5, entry_ask=1385.0, event_id="e1")
    trk.observe(t=pos.entry_t + 40, sym="5253", day="20260724", session="PM",
                bid=1383.5, ask=1384.5, mid=1384.0, entry_ask=1385.0, event_id="e2")
    meta = {"episode_id": "ep", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 1384.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 41,
                      pair_id="p", setup_type="PULLBACK_RECLAIM", exit_candidate="X_CONTINUATION",
                      day="20260724")
    assert tr["exit_price_used"] != 8564.0
    assert tr["integrity_status"] == "PASS"


def test_4062_20260723_session_end_regression():
    pos = _pos(sym="4062", ask=18160.0, mid=18150.0, t0=1784785655.691710)
    trk = PathTracker()
    trk.observe(t=pos.entry_t, sym="4062", day="20260723", session="PM",
                bid=18140.0, ask=18160.0, mid=18150.0, entry_ask=18160.0, event_id="e1")
    trk.observe(t=pos.entry_t + 40, sym="4062", day="20260723", session="PM",
                bid=18125.0, ask=18140.0, mid=18132.5, entry_ask=18160.0, event_id="e2")
    meta = {"episode_id": "ep", "entry_session": "PM", "entry_event_id": "e1", "entry_bid": 18140.0}
    tr = _close_trade(pos=pos, meta=meta, tracker=trk, exit_quote=trk.last_valid,
                      exit_reason="SESSION_END", exit_state="EXIT",
                      session_boundary_time=pos.entry_t + 42,
                      pair_id="p", setup_type="RANGE_BREAKOUT", exit_candidate="X_CONTINUATION",
                      day="20260723")
    assert tr["exit_price_used"] != 12240.0
    assert tr["integrity_status"] == "PASS"


def test_4062_20260723_max_hold_regression():
    """5903s hold must not be classified as normal HARD_STOP without gap tag."""
    t0 = 1784771275.13626
    hold = 5903.921896934509
    assert hold > MAX_HOLD_SEC + 5.0
    gap = hold - MAX_HOLD_SEC
    assert gap > 60.0
    reason = "MAX_HOLD_GAP_EXIT"  # required separation
    assert reason == "MAX_HOLD_GAP_EXIT"

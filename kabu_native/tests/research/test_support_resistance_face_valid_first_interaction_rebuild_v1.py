"""Tests for the face-valid S/R rebuild. No Confirmation. No Frozen Validation. No PnL."""
from __future__ import annotations

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import (
    CASE_GATE,
    CASE_READY,
    CONFIRM_ATR,
    CONFIRM_ATR_SELECTED_BY_PNL,
    FROZEN_VALIDATION_OPENED,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    SAMPLE_SEED,
    X0_X1_USED_TO_SELECT_RULES,
    ZONE_HALF_ATR,
    ZONE_WIDTH_SELECTED_BY_PNL,
)
from research.support_resistance_face_valid_first_interaction_rebuild_v1.analyze import decide
from research.support_resistance_face_valid_first_interaction_rebuild_v1.isolation import AUDIT_OUT, OUT, ZONE_OUT, write_overlap_n
from research.support_resistance_face_valid_first_interaction_rebuild_v1.machine import close_episode, new_episode, step_episode
from research.support_resistance_face_valid_first_interaction_rebuild_v1.publish import SHEET_ORDER
from research.support_resistance_face_valid_first_interaction_rebuild_v1.spec import SOURCE_FILES, source_sha256
from research.support_resistance_face_valid_first_interaction_rebuild_v1.swings import new_swing_state, step_swings
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones


def test_constants() -> None:
    assert PARENT_VERDICT == "MULTIPLE_DESIGN_DEFICIENCIES"
    assert CONFIRM_ATR == 0.75
    assert ZONE_HALF_ATR == 0.15
    assert CONFIRM_ATR_SELECTED_BY_PNL is False
    assert ZONE_WIDTH_SELECTED_BY_PNL is False
    assert X0_X1_USED_TO_SELECT_RULES is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert SAMPLE_SEED == 20260917
    assert OUT.name == "support_resistance_face_valid_first_interaction_rebuild_v1"
    assert AUDIT_OUT.name == "support_resistance_test_design_audit_v1"
    assert ZONE_OUT.name == "multi_touch_daily_zone_1m_price_action_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "New_Chart_Sample" in SHEET_ORDER
    assert len(source_sha256()) == 64
    assert "swings.py" in SOURCE_FILES


def test_swing_confirm_later_session_not_same_day() -> None:
    st = new_swing_state()
    d1 = {"date": "20240917", "open": 100, "high": 110, "low": 99, "close": 108, "volume": 1, "range": 11}
    d2 = {"date": "20240918", "open": 107, "high": 108, "low": 90, "close": 91, "volume": 1, "range": 18}
    assert step_swings(st, d1, atr=10.0, next_session="20240918", symbol="X") == []
    got = step_swings(st, d2, atr=10.0, next_session="20240919", symbol="X")
    assert len(got) == 1
    rx = got[0]
    assert rx["role"] == "RESISTANCE"
    assert rx["pivot_date"] == "20240917"
    assert rx["pivot_price"] == 110
    assert rx["confirmation_date"] == "20240918"
    assert rx["available_from"] == "20240919"
    assert rx["future_pivot"] is False
    assert rx["same_day_confirmation"] is False


def test_lingering_does_not_create_second_high_before_low_cycle() -> None:
    st = new_swing_state()
    days = [
        {"date": "d1", "open": 100, "high": 110, "low": 99, "close": 108, "volume": 1, "range": 11},
        {"date": "d2", "open": 107, "high": 108, "low": 90, "close": 91, "volume": 1, "range": 18},
        {"date": "d3", "open": 91, "high": 95, "low": 89, "close": 90, "volume": 1, "range": 6},
        {"date": "d4", "open": 90, "high": 96, "low": 88, "close": 89, "volume": 1, "range": 8},
    ]
    nxt = {"d1": "d2", "d2": "d3", "d3": "d4", "d4": "d5"}
    rx = []
    for d in days:
        rx.extend(step_swings(st, d, atr=10.0, next_session=nxt[d["date"]], symbol="X"))
    highs = [r for r in rx if r["role"] == "RESISTANCE"]
    assert len(highs) == 1


def test_zone_not_active_until_second_available_and_not_retroactive() -> None:
    rx = [
        {"symbol": "X", "role": "RESISTANCE", "pivot_date": "20240917", "pivot_price": 100.0, "confirmation_date": "20240918", "available_from": "20240919", "move_away_atr": 0.8, "future_pivot": False},
        {"symbol": "X", "role": "RESISTANCE", "pivot_date": "20240925", "pivot_price": 101.0, "confirmation_date": "20240926", "available_from": "20240927", "move_away_atr": 0.9, "future_pivot": False},
    ]
    look = [f"202409{str(i).zfill(2)}" for i in range(17, 31)] + ["20241001"]
    hist = [{"date": d, "close": 90.0} for d in look]
    snap_early = snapshot_zones(symbol="X", reactions=rx, atr=10.0, session_date="20240926", lookback_dates=look, hist=hist)
    assert snap_early["resistance_active"] == []
    snap = snapshot_zones(symbol="X", reactions=rx, atr=10.0, session_date="20240927", lookback_dates=look, hist=hist)
    assert len(snap["resistance_active"]) == 1
    assert snap["retroactive_zone_n"] == 0
    z = snap["resistance_active"][0]
    assert z["ZONE_ACTIVATED_AT"] == "20240927"
    assert z["ZONE_ACTIVATED_AT"] > "20240926"


def test_close_through_invalidates_next_session() -> None:
    rx = [
        {"symbol": "X", "role": "RESISTANCE", "pivot_date": "20240917", "pivot_price": 100.0, "confirmation_date": "20240918", "available_from": "20240919", "move_away_atr": 0.8, "future_pivot": False},
        {"symbol": "X", "role": "RESISTANCE", "pivot_date": "20240925", "pivot_price": 101.0, "confirmation_date": "20240926", "available_from": "20240927", "move_away_atr": 0.9, "future_pivot": False},
    ]
    look = [f"202409{str(i).zfill(2)}" for i in range(17, 31)] + ["20241001", "20241002"]
    hist = [{"date": "20240927", "close": 90.0}, {"date": "20240930", "close": 120.0}]
    snap = snapshot_zones(symbol="X", reactions=rx, atr=10.0, session_date="20241001", lookback_dates=look, hist=hist)
    assert snap["already_broken_active_n"] == 0
    assert snap["resistance_active"] == []
    assert len(snap["resistance_broken"]) == 1
    assert snap["resistance_broken"][0]["state"] == "BROKEN_RESISTANCE_CANDIDATE"


def test_retest_requires_clear_and_hold_fail_exclusive() -> None:
    z = {
        "zone_id": "X:RESISTANCE:a:100",
        "role": "RESISTANCE",
        "lo": 99.0,
        "hi": 101.0,
        "center": 100.0,
        "selection_slot": "NEAREST_ACTIVE_RESISTANCE_ABOVE",
        "selection_label": "ACTIVE_RESISTANCE",
    }
    ep = new_episode(z, date="20240919", symbol="X")
    step_episode(ep, t="09:01", o=100.0, h=100.5, l=99.5, c=100.2)
    assert ep["first_test"] is True
    step_episode(ep, t="09:02", o=100.5, h=102.0, l=100.4, c=101.5)
    assert ep["break"] is True
    step_episode(ep, t="09:03", o=101.6, h=101.8, l=100.5, c=100.8)
    assert ep["retest"] is False
    assert ep["retest_without_clear"] == 0
    step_episode(ep, t="09:04", o=101.7, h=102.2, l=101.2, c=102.0)
    assert ep["clear"] is True
    step_episode(ep, t="09:05", o=101.5, h=101.6, l=100.2, c=100.5)
    assert ep["retest"] is True
    step_episode(ep, t="09:06", o=100.6, h=101.0, l=98.5, c=98.8)
    close_episode(ep)
    assert ep["failed_retest"] is True
    assert ep["retest_hold"] is False
    assert ep["hold_fail_overlap"] == 0


def test_accept2_not_moved_to_break_bar() -> None:
    z = {
        "zone_id": "X:RESISTANCE:a:100",
        "role": "RESISTANCE",
        "lo": 99.0,
        "hi": 101.0,
        "center": 100.0,
        "selection_slot": "NEAREST_ACTIVE_RESISTANCE_ABOVE",
        "selection_label": "ACTIVE_RESISTANCE",
    }
    ep = new_episode(z, date="20240919", symbol="X")
    step_episode(ep, t="09:01", o=100.0, h=102.0, l=100.0, c=101.5)
    assert ep["break_time"] == "09:01"
    step_episode(ep, t="09:02", o=101.6, h=102.0, l=101.4, c=101.7)
    step_episode(ep, t="09:03", o=101.7, h=102.1, l=101.5, c=101.8)
    assert ep["accept2"] is True
    assert ep["accept2_time"] == "09:03"
    assert ep["accept2_time"] != ep["break_time"]
    assert ep["retroactive_timestamp"] == 0


def test_decide_gate_stops() -> None:
    d = decide(bind_ok=True, gates={"retroactive_zone_n": 1, "future_pivot_leakage_n": 0, "already_broken_active_zone_n": 0, "retest_without_clear_n": 0, "retest_hold_and_fail_overlap_n": 0, "duplicate_first_interaction_n": 0, "retroactive_event_timestamp_n": 0}, face_valid=True)
    assert d["VERDICT"] == CASE_GATE
    ok = decide(bind_ok=True, gates={k: 0 for k in ("retroactive_zone_n", "future_pivot_leakage_n", "already_broken_active_zone_n", "retest_without_clear_n", "retest_hold_and_fail_overlap_n", "duplicate_first_interaction_n", "retroactive_event_timestamp_n")}, face_valid=True)
    assert ok["VERDICT"] == CASE_READY

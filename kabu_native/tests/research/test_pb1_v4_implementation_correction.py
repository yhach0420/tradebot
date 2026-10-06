"""Tests for V4 implementation correction. No PnL. Leaked SHA immutable."""
from __future__ import annotations

import inspect

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_implementation_correction import (
    CASE_CORRECTED,
    CASE_INCOMPLETE,
    ONE_M_CAN_CREATE_ELIGIBILITY,
    PARENT_V32_SHA,
    PRIMARY_SETUP_TIMEFRAME,
    V4_LEGACY_LEAKED_IMPLEMENTATION,
)
from research.pb1_v4_implementation_correction.analyze import decide
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction.isolation import LEAKED_OUT, OUT, write_overlap_n
from research.pb1_v4_implementation_correction.leakage import scan_eligibility_leakage
from research.pb1_v4_implementation_correction.location import classify_s2
from research.pb1_v4_implementation_correction.machine import commit_s2_s3, new_side
from research.pb1_v4_implementation_correction.s1 import classify_s1, extend_failed_open, s1_pass
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_machine_implementation.execution import classify_e1
from research.pb1_v4_machine_implementation.s0 import classify_s0


def _bar(t0: str, t1: str, o: float, h: float, l: float, c: float) -> dict:
    rng = h - l
    body = abs(c - o)
    return {
        "t0": t0,
        "t1": t1,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "range": rng,
        "body": body,
        "body_over_range": body / rng if rng else 0.0,
        "direction": 1 if c > o else (-1 if c < o else 0),
        "net": c - o,
    }


def test_leaked_sha_immutable_and_new_sha_distinct():
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert corrected_machine_sha256() != V4_LEGACY_LEAKED_IMPLEMENTATION
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_implementation_correction" in str(OUT).replace("\\", "/")
    assert "pb1_v4_machine_implementation" in str(LEAKED_OUT).replace("\\", "/")
    assert str(OUT.resolve()) != str(LEAKED_OUT.resolve())
    assert ONE_M_CAN_CREATE_ELIGIBILITY is False
    assert PRIMARY_SETUP_TIMEFRAME == "5m"


def test_eligibility_leakage_zero():
    leak = scan_eligibility_leakage()
    assert leak["legacy_eligibility_leakage_n"] == 0
    src = inspect.getsource(classify_s2)
    assert "NO_MEANINGFUL_ROOM" not in src
    assert "STRUCTURALLY_BLOCKED" not in src
    assert "classify_zone_path" not in src
    assert "STRUCTURAL_ROUTE_R_MULT" not in src


def test_true_cannot_be_created_from_later_bars():
    open_bars = [
        _bar("09:00", "09:04", 100.0, 100.3, 99.9, 100.1),
        _bar("09:05", "09:09", 100.1, 100.4, 99.95, 100.15),
        _bar("09:10", "09:14", 100.15, 100.35, 100.0, 100.2),
    ]
    locked = classify_s1(open_bars, normal_opening_5m=2.0)
    assert locked["ok"] is False
    assert locked["state"] != "TRUE_OPENING_DRIVE"
    extra = [
        _bar("09:15", "09:19", 100.2, 103.0, 100.1, 102.8),
        _bar("09:20", "09:24", 102.8, 104.5, 102.6, 104.2),
        _bar("09:25", "09:29", 104.2, 105.8, 104.0, 105.5),
    ]
    later = classify_s1(open_bars + extra, normal_opening_5m=2.0)
    assert later["state"] != "TRUE_OPENING_DRIVE"
    assert later.get("opening_three_only") or later["ok"] is False
    ext = extend_failed_open(locked, extra, open_bars=open_bars, normal_opening_5m=2.0)
    assert ext.get("state") != "TRUE_OPENING_DRIVE"
    assert ext.get("true_from_later_bars") is False


def test_failed_open_3382_shape_still_passes():
    bars = [
        _bar("09:00", "09:04", 100.0, 104.2, 99.8, 103.6),
        _bar("09:05", "09:09", 103.6, 103.8, 101.0, 101.4),
        _bar("09:10", "09:14", 101.4, 101.6, 100.2, 100.5),
    ]
    d = classify_s1(bars, normal_opening_5m=2.0)
    assert d["ok"] is True
    assert d["state"] == "FAILED_OPEN_THEN_REAL_DRIVE"
    assert d["DIR"] == -1
    assert s1_pass(d)


def test_tiny_dip_not_failed_open():
    bars = [
        _bar("09:00", "09:04", 100.0, 100.2, 99.7, 99.85),
        _bar("09:05", "09:09", 99.85, 101.0, 99.8, 100.7),
        _bar("09:10", "09:14", 100.7, 101.3, 100.5, 101.1),
    ]
    d = classify_s1(bars, normal_opening_5m=2.0)
    assert d["ok"] is False
    assert d["state"] in ("FLAT_OR_CRAWL", "MICRO_OR_LEAK", "TWO_SIDED_OPEN")


def test_s3_never_without_s2_on_location_fail():
    st = new_side(-1)
    st.s1 = True
    st.retest_pos = 10
    st.retest_t = "09:40"
    st.retest_high = 100.2
    st.retest_low = 99.8
    st.five_m_left = False
    bar = _bar("09:40", "09:44", 100.1, 100.3, 99.9, 100.05)
    commit_s2_s3(
        st,
        close=100.05,
        or_high=101.0,
        or_low=99.0,
        n1m=0.4,
        zones=[],
        five_m_bar=bar,
        pdh=None,
        pdl=None,
        pdc=None,
        vwap=None,
        sma25=None,
        sma75=None,
        atr=2.0,
    )
    assert st.s2 is False
    assert st.s3 is False
    assert st.dead is True


def test_cleared_zone_retest_family_a_no_room_gate():
    zone = {"zone_low": 100.0, "zone_high": 100.5, "role": "RESISTANCE", "name": "z1", "known_at": "prior"}
    bar = _bar("09:40", "09:44", 100.7, 100.8, 100.45, 100.72)
    d = classify_s2(
        sign=1,
        close=100.72,
        or_high=99.8,
        or_low=99.0,
        break_t="09:25",
        break_close=101.2,
        max_fav=101.4,
        retest_t="09:40",
        retest_high=100.8,
        retest_low=100.45,
        five_m_left=True,
        five_m_bar=bar,
        n1m=0.2,
        zones=[zone],
    )
    assert d["ok"] is True
    assert d["family"] == "CLEARED_ZONE_RETEST"
    assert "NO_MEANINGFUL_ROOM" not in str(d)


def test_or_held_forbidden_when_drive_enters_uncleared_zone():
    zone = {"zone_low": 100.7, "zone_high": 101.4, "role": "RESISTANCE", "name": "overhead", "known_at": "prior"}
    bar = _bar("09:40", "09:44", 100.6, 100.8, 100.48, 100.7)
    d = classify_s2(
        sign=1,
        close=100.7,
        or_high=100.5,
        or_low=99.5,
        break_t="09:25",
        break_close=101.0,
        max_fav=101.0,
        retest_t="09:40",
        retest_high=100.8,
        retest_low=100.45,
        five_m_left=True,
        five_m_bar=bar,
        n1m=0.2,
        zones=[zone],
    )
    assert d["ok"] is False
    assert d["family"] is None
    assert d["reason"] in ("OR_RETEST_NOT_PURE_AND_NO_A_OR_C", "NO_DEFENSIBLE_LOCATION")


def test_e1_cannot_revive_and_s0_unchanged():
    rec = {"c": [100.0, 101.0, 103.0]}
    d = classify_e1(
        sign=1,
        rec=rec,
        pos=2,
        open_px=101.0,
        high=104.0,
        low=100.5,
        close=103.5,
        retest_high=101.0,
        retest_low=100.0,
        n1m=1.0,
        setup_eligible=False,
        five_m_setup_valid=False,
        thesis_lost=False,
    )
    assert d["ok"] is False
    assert d["revived_rejected_setup"] is False
    flat = [_bar("09:00", "09:04", 100.0, 100.4, 100.0, 100.2)] * 3
    s0 = classify_s0(bars_open=flat, normal_opening_5m=2.0, abs_gap_atr=0.10, xs_rank_pct=0.99, tv_0915_pctl=0.99)
    assert s0["ok"] is False


def test_decide_incomplete_on_leakage():
    d = decide(
        bind_ok=True,
        leakage_n=1,
        inv={
            "S3_without_S2_n": 0,
            "S1_without_S0_n": 0,
            "S2_without_S1_n": 0,
            "S4_without_S3_n": 0,
            "state_identity_mismatch_n": 0,
            "true_from_later_bars_n": 0,
            "same_bar_entry_n": 0,
            "e0_requires_s4": True,
            "e1_requires_s4": True,
        },
        audit={"3382_20241004": {"machine_S1": True, "machine_opening_state": "FAILED_OPEN_THEN_REAL_DRIVE"}},
        leaked_live=V4_LEGACY_LEAKED_IMPLEMENTATION,
        leaked_preserved=True,
        monotonic=True,
    )
    assert d["VERDICT"] == CASE_INCOMPLETE
    d2 = decide(
        bind_ok=True,
        leakage_n=0,
        inv={
            "S3_without_S2_n": 0,
            "S1_without_S0_n": 0,
            "S2_without_S1_n": 0,
            "S4_without_S3_n": 0,
            "state_identity_mismatch_n": 0,
            "true_from_later_bars_n": 0,
            "same_bar_entry_n": 0,
            "e0_requires_s4": True,
            "e1_requires_s4": True,
        },
        audit={
            "3382_20241004": {"machine_S1": True, "machine_opening_state": "FAILED_OPEN_THEN_REAL_DRIVE"},
            "7011_20241205": {"machine_S1": False},
            "6920_20250404": {"machine_S1": False},
            "9501_20251113": {"machine_S1": False},
            "6787_20250214": {"machine_S2": False, "machine_S4": False},
            "9432_20250402": {"machine_S2": False, "machine_S1": True},
            "6963_20250613": {"machine_S2": True, "location_family": "CLEARED_ZONE_RETEST"},
            "6857_20250930": {"machine_S2": True, "location_family": "CLEARED_ZONE_RETEST"},
        },
        leaked_live=V4_LEGACY_LEAKED_IMPLEMENTATION,
        leaked_preserved=True,
        monotonic=True,
    )
    assert d2["VERDICT"] == CASE_CORRECTED

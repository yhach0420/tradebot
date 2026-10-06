"""Tests for PB1 V4 machine. No PnL. Parents frozen. 1m cannot create eligibility."""
from __future__ import annotations

import inspect

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_machine_implementation import (
    E1_NET_N1M,
    ONE_M_CAN_CREATE_ELIGIBILITY,
    PARENT_V32_SHA,
    PRIMARY_SETUP_TIMEFRAME,
    TRADINGVALUE_IS_1M_TRIGGER_GATE,
    V4_IS_1M_STRATEGY,
)
from research.pb1_v4_machine_implementation.analyze import decide
from research.pb1_v4_machine_implementation.execution import classify_e1
from research.pb1_v4_machine_implementation.isolation import OUT, V32_OUT, write_overlap_n
from research.pb1_v4_machine_implementation.machine import step_break_retest
from research.pb1_v4_machine_implementation.s0 import classify_s0
from research.pb1_v4_machine_implementation.s1 import classify_s1
from research.pb1_v4_machine_implementation.s4 import classify_s4


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


def test_parents_frozen_and_invariants():
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert V4_IS_1M_STRATEGY is False
    assert ONE_M_CAN_CREATE_ELIGIBILITY is False
    assert PRIMARY_SETUP_TIMEFRAME == "5m"
    assert TRADINGVALUE_IS_1M_TRIGGER_GATE is False
    assert E1_NET_N1M != 2.0
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_machine_implementation" in str(OUT).replace("\\", "/")
    assert "pb1_v3_2_opening_drive_and_reacceleration_semantics" in str(V32_OUT).replace("\\", "/")
    src = (
        inspect.getsource(step_break_retest)
        + inspect.getsource(decide)
        + inspect.getsource(classify_s1)
        + inspect.getsource(classify_e1)
    )
    assert "profit_factor" not in src.lower()
    assert "09:30" not in src
    assert "09:45" not in src
    assert "or_close_loc" not in inspect.getsource(classify_s1)
    assert "OR_CLOSE_HALF" not in inspect.getsource(classify_s1)
    d = decide(bind_ok=True, same_bar_n=0, confirmation_opened=False, fv_opened=False)
    assert d["VERDICT"] == "PB1_V4_MACHINE_FROZEN_WAIT_FOR_PROSPECTIVE_FACE_V1"
    assert d["one_m_can_create_eligibility"] is False


def test_tiny_dip_or_half_fails_s1():
    bars = [
        _bar("09:00", "09:04", 100.0, 100.2, 99.7, 99.85),
        _bar("09:05", "09:09", 99.85, 101.0, 99.8, 100.7),
        _bar("09:10", "09:14", 100.7, 101.3, 100.5, 101.1),
    ]
    d = classify_s1(bars, normal_opening_5m=2.0)
    assert d["ok"] is False
    assert d["state"] in ("FLAT_OR_CRAWL", "MICRO_OR_LEAK", "TWO_SIDED_OPEN")
    assert d["or_half_used"] is False


def test_failed_open_3382_shape_passes_from_failed_extreme():
    bars = [
        _bar("09:00", "09:04", 100.0, 104.2, 99.8, 103.6),
        _bar("09:05", "09:09", 103.6, 103.8, 101.0, 101.4),
        _bar("09:10", "09:14", 101.4, 101.6, 100.2, 100.5),
    ]
    d = classify_s1(bars, normal_opening_5m=2.0)
    assert d["ok"] is True
    assert d["state"] == "FAILED_OPEN_THEN_REAL_DRIVE"
    assert d["DIR"] == -1
    assert d["or_half_used"] is False


def test_true_opening_drive_same_dir():
    bars = [
        _bar("09:00", "09:04", 100.0, 102.4, 99.9, 102.1),
        _bar("09:05", "09:09", 102.1, 103.6, 101.8, 103.3),
        _bar("09:10", "09:14", 103.3, 104.8, 103.0, 104.5),
    ]
    d = classify_s1(bars, normal_opening_5m=2.0)
    assert d["ok"] is True
    assert d["state"] == "TRUE_OPENING_DRIVE"
    assert d["DIR"] == 1


def test_s0_rejects_tv_or_xs_alone_and_flat():
    flat = [_bar("09:00", "09:04", 100.0, 100.4, 100.0, 100.2)] * 3
    d = classify_s0(bars_open=flat, normal_opening_5m=2.0, abs_gap_atr=0.10, xs_rank_pct=0.99, tv_0915_pctl=0.99)
    assert d["ok"] is False
    assert d["xs_alone_sufficient"] is False
    assert d["tv_alone_sufficient"] is False


def test_e1_cannot_revive_rejected_setup():
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
    assert d["ONE_M_ENTRY_ALLOWED"] is False
    assert d["revived_rejected_setup"] is False
    assert d["tv_gated"] is False


def test_s4_not_huge_breakout_and_visible():
    bar = _bar("09:20", "09:24", 100.0, 101.2, 99.9, 101.1)
    d = classify_s4(sign=1, bar=bar, retest_high=100.4, retest_low=99.8, normal_opening_5m=2.0, n1m=0.4)
    assert d["huge_breakout_required"] is False
    assert d["ok"] is True

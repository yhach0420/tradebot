"""Tests for PB1 opening-range causal path test V1. No PnL. V2 not mutated."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_causal_path_test_v1 import (
    CASE_NONE,
    CASE_SUPPORTED,
    CASE_UNSTABLE,
    EXPECTED_SETUP_N,
    NEXT_COMPLETE,
    NEXT_RCA,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
)
from research.pb1_opening_range_causal_path_test_v1.analyze import decide
from research.pb1_opening_range_causal_path_test_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.pb1_opening_range_causal_path_test_v1.match import find_control, match_all
from research.pb1_opening_range_causal_path_test_v1.path import (
    _or_accept_fail,
    _retest_breach,
    structural_r,
    target_semantics,
)
from research.pb1_opening_range_causal_path_test_v1.publish import SHEET_ORDER
from research.pb1_opening_range_causal_path_test_v1.spec import source_sha256
from research.pb1_opening_range_causal_path_test_v1.walk import emit_treated_and_eligible
from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256


def test_parent_v2_frozen_and_not_mutated():
    assert PARENT_VERDICT == "PB1_OPENING_RANGE_FACE_VALID_READY_V2"
    assert PARENT_PLAYBOOK_MACHINE_SHA256 == "3ebe0220fd9cb1e5749e39833fa1bab0146759cce5dc9daaacf0e3b3d3daf3fd"
    assert v2_machine_sha256() == PARENT_PLAYBOOK_MACHINE_SHA256
    assert EXPECTED_SETUP_N == 465
    assert source_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_opening_range_causal_path_test_v1" in str(OUT).replace("\\", "/")
    assert "pb1_opening_range_continuation_face_valid_v2" in str(PARENT_OUT).replace("\\", "/")
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "D2" in SHEET_ORDER and "D3" in SHEET_ORDER and "D4" in SHEET_ORDER


def test_or_fail_is_close_not_wick_and_r_invalid():
    assert _or_accept_fail(100.9, 101.0, 100.0, 1) is True
    assert _or_accept_fail(101.1, 101.0, 100.0, 1) is False
    assert _or_accept_fail(100.1, 101.0, 100.0, -1) is True
    assert _or_accept_fail(99.9, 101.0, 100.0, -1) is False
    assert _retest_breach(101.0, 99.4, 101.2, 99.5, 1) is True
    assert _retest_breach(101.0, 99.6, 101.2, 99.5, 1) is False
    bad = structural_r(sign=1, entry=100.0, retest_high=101.0, retest_low=100.5)
    assert bad["risk_state"] == "RISK_INVALID"
    ok = structural_r(sign=1, entry=101.0, retest_high=101.2, retest_low=100.5)
    assert ok["risk_state"] == "RISK_DEFINED"
    none = target_semantics(sign=1, entry=101.0, levels={"PDH": 100.0, "PDL": 99.0}, r_px=0.5)
    assert none["target_kind"] == "NO_PREKNOWN_TARGET_AHEAD"
    ahead = target_semantics(sign=1, entry=101.0, levels={"PDH": 102.0, "VWAP": 101.5}, r_px=0.5)
    assert ahead["target_kind"] == "TARGET_AHEAD"


def test_matching_does_not_use_treatment_or_drop_later_triggers():
    match_src = inspect.getsource(find_control) + inspect.getsource(match_all)
    for forbidden in ("abs_gap", "impulse_mag", "break_beyond", "away_n", "tv_sequence", "room_class", "gap_signed"):
        assert forbidden not in match_src
    assert "str(trig) <= t" in match_src
    walk_src = inspect.getsource(emit_treated_and_eligible)
    assert "v1_step" not in walk_src
    assert "same_bar_entry" in walk_src
    ana = inspect.getsource(decide)
    assert "profit_factor" not in ana.lower()
    assert "DecisionTree" not in ana
    assert "occupancy" not in ana.lower()


def test_decide_supported_vs_rca_not_autostop():
    by = {b: {"r10_bps": {"p50": 2.0}} for b in ("D2", "D3", "D4")}
    inc = {b: {"matched_n": 40, "gap_r10_bps": {"p50": 3.0}} for b in ("D2", "D3", "D4")}
    sup = decide(
        bind_ok=True,
        identity_ok=True,
        by_block=by,
        inc_block=inc,
        match={"match_rate": 0.7},
        cost={"path_magnitude_plausibly_exceeds_cost_scale": True},
    )
    assert sup["VERDICT"] == CASE_SUPPORTED
    assert sup["NEXT"] == NEXT_COMPLETE
    assert sup["STOP_PB1"] is False
    mixed = decide(
        bind_ok=True,
        identity_ok=True,
        by_block={"D2": {"r10_bps": {"p50": 4.0}}, "D3": {"r10_bps": {"p50": -2.0}}, "D4": {"r10_bps": {"p50": 1.0}}},
        inc_block={
            "D2": {"matched_n": 40, "gap_r10_bps": {"p50": -1.0}},
            "D3": {"matched_n": 40, "gap_r10_bps": {"p50": 1.0}},
            "D4": {"matched_n": 40, "gap_r10_bps": {"p50": 0.2}},
        },
        match={"match_rate": 0.7},
        cost={"path_magnitude_plausibly_exceeds_cost_scale": False},
    )
    assert mixed["VERDICT"] == CASE_UNSTABLE
    assert mixed["NEXT"] == NEXT_RCA
    assert mixed["STOP_PB1"] is False
    none = decide(
        bind_ok=True,
        identity_ok=True,
        by_block={b: {"r10_bps": {"p50": -3.0}} for b in ("D2", "D3", "D4")},
        inc_block={b: {"matched_n": 40, "gap_r10_bps": {"p50": -1.0}} for b in ("D2", "D3", "D4")},
        match={"match_rate": 0.7},
        cost={"path_magnitude_plausibly_exceeds_cost_scale": False},
    )
    assert none["VERDICT"] == CASE_NONE
    assert none["NEXT"] == NEXT_RCA
    assert none["STOP_PB1"] is False

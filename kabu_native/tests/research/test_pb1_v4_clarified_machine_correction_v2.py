"""Tests for clarified-machine correction V2. No PnL. Frozen parents immutable."""
from __future__ import annotations

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_clarified_machine_correction_v2 import (
    CASE_IMPLEMENTED,
    CASE_INCOMPLETE,
    EXPECTED_SPEC_SHA256,
    PARENT_CLARIFIED_MACHINE_SHA256,
    PARENT_V32_SHA,
    PRIMARY_SETUP_TIMEFRAME,
)
from research.pb1_v4_clarified_machine_correction_v2.active import classify_progress
from research.pb1_v4_clarified_machine_correction_v2.analyze import decide
from research.pb1_v4_clarified_machine_correction_v2.baselines import SameClockOpeningHistory
from research.pb1_v4_clarified_machine_correction_v2.binding import implementation_binding
from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v2.isolation import OUT, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v2.leakage import scan_package
from research.pb1_v4_clarified_machine_correction_v2.location import classify_interaction, identify_location
from research.pb1_v4_clarified_machine_correction_v2.seed import classify_seed
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


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


def _clock(med: float = 2.0) -> dict:
    return {
        "same_clock": [{"median": med, "clock": "x"} for _ in range(3)],
        "mean_clock_median": med,
        "first_bar_only_used_for_all_three": False,
    }


def test_parents_and_new_sha_distinct():
    assert spec_sha256() == EXPECTED_SPEC_SHA256
    assert leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION
    assert corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256
    assert parent_machine_sha256() == PARENT_CLARIFIED_MACHINE_SHA256
    sha = machine_sha256()
    assert sha != V4_LEGACY_LEAKED_IMPLEMENTATION
    assert sha != V4_CORRECTED_MACHINE_SHA256
    assert sha != PARENT_CLARIFIED_MACHINE_SHA256
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v2" in str(OUT).replace("\\", "/")
    assert PRIMARY_SETUP_TIMEFRAME == "5m"
    bind = implementation_binding()
    assert bind["A_first_unsuccessful_interaction"]["authoritative_value"] is True
    leak = scan_package()
    assert leak["FAIL_EXTEND_present"] is False
    assert leak["LAST_BREAK_present"] is False
    assert leak["SETUP_ELIGIBLE_in_eligibility"] is False


def test_same_clock_history_not_first_bar_only():
    h = SameClockOpeningHistory()
    assert h.median("7203", "09:00", "09:04") is None


def test_failed_open_3382_path_not_true_first():
    bars = [
        _bar("09:00", "09:04", 100.0, 104.2, 99.8, 103.6),
        _bar("09:05", "09:09", 103.6, 103.8, 101.0, 101.4),
        _bar("09:10", "09:14", 101.4, 101.6, 99.5, 99.7),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] == "FAILED_OPEN_SEED"
    assert d["true_first"] is False
    assert d["DIR"] == -1
    assert d.get("failed_open", {}).get("form") == "DIRECTIONAL_FAILED_ATTEMPT"
    assert d.get("opposite_established_at_seed") is True


def test_wide_rejection_failed_open_7011_shape():
    bars = [
        _bar("09:00", "09:04", 100.0, 103.0, 97.0, 99.9),
        _bar("09:05", "09:09", 99.9, 102.4, 99.7, 102.2),
        _bar("09:10", "09:14", 102.2, 103.1, 101.8, 102.8),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] == "FAILED_OPEN_SEED"
    assert d["failed_open"]["form"] == "WIDE_REJECTION_FAILED_ATTEMPT"
    assert d["failed_open"]["wide_rejection"] is True


def test_wide_hammer_is_not_failed_open():
    bars = [
        _bar("09:00", "09:04", 100.0, 100.2, 97.0, 100.15),
        _bar("09:05", "09:09", 100.15, 101.5, 100.1, 101.3),
        _bar("09:10", "09:14", 101.3, 102.0, 101.2, 101.8),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] != "FAILED_OPEN_SEED"


def test_one_bar_without_followthrough_not_true():
    bars = [
        _bar("09:00", "09:04", 100.0, 104.0, 99.8, 103.5),
        _bar("09:05", "09:09", 103.5, 103.7, 103.3, 103.55),
        _bar("09:10", "09:14", 103.55, 103.7, 103.5, 103.58),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] != "TRUE_OPENING_DRIVE_SEED"


def test_substantial_counter_not_erased_by_final_close():
    bars = [
        _bar("09:00", "09:04", 100.0, 108.0, 99.5, 107.0),
        _bar("09:05", "09:09", 107.0, 107.2, 101.0, 101.5),
        _bar("09:10", "09:14", 101.5, 109.0, 101.4, 108.5),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] == "NO_VALID_DRIVE_SEED"
    assert d["subtype"] == "TWO_SIDED_OPEN"


def test_middle_only_pullback_may_remain_true():
    bars = [
        _bar("09:00", "09:04", 100.0, 103.0, 99.8, 102.5),
        _bar("09:05", "09:09", 102.5, 102.6, 102.3, 102.45),
        _bar("09:10", "09:14", 102.45, 105.0, 102.4, 104.6),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d.get("intent", {}).get("middle_only_pullback") is True
    assert d["seed"] == "TRUE_OPENING_DRIVE_SEED"


def test_vwap_cannot_mint_family_a():
    bar = _bar("09:10", "09:14", 101.0, 103.0, 100.8, 102.8)
    loc = identify_location(
        sign=1,
        active=True,
        bar=bar,
        or_high=104.0,
        or_low=99.0,
        zones=[{"name": "VWAP", "kind": "VWAP", "zone_low": 100.0, "zone_high": 100.4}],
        pdh=None,
        pdl=None,
        pdc=None,
        vwap=100.2,
        n1m=0.4,
        left=False,
        session_open=101.0,
    )
    assert loc is None or loc.get("family") != "A_CLEARED_ZONE"


def test_or_location_identity_does_not_need_hold():
    bar = _bar("09:40", "09:44", 101.2, 101.4, 100.6, 100.7)
    loc = identify_location(
        sign=1,
        active=True,
        bar=bar,
        or_high=100.5,
        or_low=99.5,
        zones=[],
        pdh=None,
        pdl=None,
        pdc=None,
        vwap=None,
        n1m=0.4,
        left=True,
    )
    assert loc is not None
    assert loc["hold_required_to_identify"] is False
    assert loc["created_by_1m"] is False
    first = classify_interaction(sign=1, high=100.6, low=100.4, close=100.45, level=100.5, prev="NO_INTERACTION_YET")
    assert first["kills_thesis"] is False


def test_marginal_extreme_does_not_reset_stall():
    prev = _bar("09:15", "09:19", 100.0, 101.0, 99.5, 100.4)
    bar = _bar("09:20", "09:24", 100.4, 101.2, 99.6, 100.5)
    prog = classify_progress(sign=1, bar=bar, prev_bar=prev, prev_ext=101.0)
    assert prog["class"] in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME", "NO_DIRECTIONAL_PROGRESS")
    assert prog["resets_stall"] is False


def test_decide_incomplete_on_invariants():
    d = decide(
        bind_ok=True,
        inv={"violations": 1, "SAME_BAR_ENTRY": 0},
        leak={"FAIL_EXTEND_present": False, "LAST_BREAK_present": False, "SETUP_ELIGIBLE_in_eligibility": False},
        audit={},
        new_sha="abc",
        hidden_parity=True,
    )
    assert d["VERDICT"] == CASE_INCOMPLETE
    d2 = decide(
        bind_ok=True,
        inv={"violations": 0, "SAME_BAR_ENTRY": 0},
        leak={"FAIL_EXTEND_present": False, "LAST_BREAK_present": False, "SETUP_ELIGIBLE_in_eligibility": False},
        audit={
            "3382_20241004": {"machine_SEED": "FAILED_OPEN_SEED", "machine_ACTIVE": True},
            "7011_20250523": {"machine_SEED": "FAILED_OPEN_SEED", "machine_ACTIVE": True},
            "4063_20251118": {"machine_SEED": "FAILED_OPEN_SEED"},
        },
        new_sha="abc",
        hidden_parity=True,
        hidden_recompute={"mismatch_n": 0, "HIDDEN_1M_THESIS_PARITY_recomputed": True},
    )
    assert d2["VERDICT"] == CASE_IMPLEMENTED

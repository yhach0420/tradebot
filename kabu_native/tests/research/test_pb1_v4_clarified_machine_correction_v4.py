"""Tests for clarified-machine correction V4. No PnL. Frozen parents immutable."""
from __future__ import annotations

from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v3.definitions import machine_sha256 as correction_v3_sha256
from research.pb1_v4_clarified_machine_correction_v4 import (
    CASE_INCOMPLETE,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_CORRECTION_V3_SHA256,
    EXPECTED_SPEC_SHA256,
    PARENT_CLARIFIED_MACHINE_SHA256,
    PARENT_V32_SHA,
    PRIMARY_SETUP_TIMEFRAME,
)
from research.pb1_v4_clarified_machine_correction_v4.active import (
    classify_active_loss,
    classify_failed_break_reaccepted,
    classify_progress,
    note_failed_probe_pending,
)
from research.pb1_v4_clarified_machine_correction_v4.analyze import decide
from research.pb1_v4_clarified_machine_correction_v4.baselines import SameClockOpeningHistory
from research.pb1_v4_clarified_machine_correction_v4.binding import implementation_binding
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.encoding import classify_post_dominant, classify_sandwich_counter
from research.pb1_v4_clarified_machine_correction_v4.isolation import OUT, write_overlap_n
from research.pb1_v4_clarified_machine_correction_v4.leakage import scan_package
from research.pb1_v4_clarified_machine_correction_v4.location import classify_interaction, identify_location
from research.pb1_v4_clarified_machine_correction_v4.seed import classify_seed
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
    assert correction_v2_sha256() == EXPECTED_CORRECTION_V2_SHA256
    assert correction_v3_sha256() == EXPECTED_CORRECTION_V3_SHA256
    sha = machine_sha256()
    assert sha != V4_LEGACY_LEAKED_IMPLEMENTATION
    assert sha != V4_CORRECTED_MACHINE_SHA256
    assert sha != PARENT_CLARIFIED_MACHINE_SHA256
    assert sha != EXPECTED_CORRECTION_V2_SHA256
    assert sha != EXPECTED_CORRECTION_V3_SHA256
    assert v32_machine_sha256() == PARENT_V32_SHA
    assert write_overlap_n("", "") == 0
    assert "pb1_v4_clarified_machine_correction_v4" in str(OUT).replace("\\", "/")
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


def test_wide_rejection_failed_open_7011_shape():
    bars = [
        _bar("09:00", "09:04", 100.0, 103.0, 97.0, 99.9),
        _bar("09:05", "09:09", 99.9, 102.4, 99.7, 102.2),
        _bar("09:10", "09:14", 102.2, 103.1, 101.8, 102.8),
    ]
    d = classify_seed(bars, clock_snap=_clock(2.0), atr20=5.0)
    assert d["seed"] == "FAILED_OPEN_SEED"
    assert d["failed_open"]["form"] == "WIDE_REJECTION_FAILED_ATTEMPT"


def test_absorption_nick_is_not_real_followthrough():
    bars = [
        _bar("09:00", "09:04", 2395.0, 2441.5, 2382.5, 2430.0),
        _bar("09:05", "09:09", 2431.0, 2443.0, 2423.5, 2434.5),
        _bar("09:10", "09:14", 2434.5, 2450.0, 2434.5, 2445.5),
    ]
    post = classify_post_dominant(bars, sign=1)
    assert post["post_dominant_class"] == "INITIAL_DISPLACEMENT_WITH_ABSORPTION"
    d = classify_seed(bars, clock_snap=_clock(30.0), atr20=40.0)
    assert d["seed"] != "TRUE_OPENING_DRIVE_SEED"
    assert (d.get("intent") or {}).get("post_dominant", {}).get("post_dominant_class") == "INITIAL_DISPLACEMENT_WITH_ABSORPTION"


def test_6963_intact_pullback_not_body_only_two_sided():
    bars = [
        _bar("09:00", "09:04", 100.0, 100.0, 63.0, 63.0),
        _bar("09:05", "09:09", 63.0, 74.5, 63.0, 67.5),
        _bar("09:10", "09:14", 67.5, 67.5, 30.0, 32.0),
    ]
    sand = classify_sandwich_counter(bars, sign=-1)
    assert sand["semantic_class"] == "PULLBACK_ORIGINAL_AUCTION_INTACT"
    d = classify_seed(bars, clock_snap=_clock(10.0), atr20=20.0)
    assert d.get("intent", {}).get("two_sided_balance") is False


def test_7741_meaningful_counter_not_rescued():
    bars = [
        _bar("09:00", "09:04", 100.0, 123.0, 99.0, 117.5),
        _bar("09:05", "09:09", 117.5, 118.0, 108.0, 111.5),
        _bar("09:10", "09:14", 111.5, 116.5, 111.0, 116.0),
    ]
    sand = classify_sandwich_counter(bars, sign=1)
    assert sand["semantic_class"] in ("MEANINGFUL_COUNTER_AUCTION", "TWO_SIDED_COMMITTED_FIGHT")
    d = classify_seed(bars, clock_snap=_clock(10.0), atr20=20.0)
    assert d["seed"] != "TRUE_OPENING_DRIVE_SEED"


def test_n3_pause_is_not_stale():
    prev = _bar("09:15", "09:19", 100.0, 101.0, 99.5, 100.8)
    bar = _bar("09:20", "09:24", 100.8, 101.1, 100.6, 100.9)
    loss = classify_active_loss(
        sign=1,
        close=100.9,
        or_high=102.0,
        or_low=99.0,
        open_0900=100.0,
        peak_disp=2.0,
        wick_only_n=0,
        micro_break_n=0,
        recross_closes=0,
        left=False,
        five_m_no_expansion_n=3,
        both_or_extremes_revisited=False,
        location_identified=True,
        had_renewed_auction=True,
        bar=bar,
        prev_bar=prev,
    )
    assert "STALE_RANGE_RESOLUTION" not in (loss.get("flags") or [])
    assert "FAILED_BREAK_REACCEPTED" not in (loss.get("flags") or [])
    assert loss.get("n_bar_expiry") is False
    assert loss.get("location_gated") is False


def test_failed_break_reaccepted_at_confirmation_not_probe():
    extra: dict = {}
    probe = _bar("09:25", "09:29", 3107.0, 3130.0, 3106.0, 3108.0)
    opp = _bar("09:30", "09:34", 3108.0, 3112.0, 3085.0, 3101.0)
    mid = _bar("09:35", "09:39", 3101.0, 3125.0, 3100.0, 3106.0)
    conf = _bar("09:40", "09:44", 3106.0, 3123.0, 3106.0, 3118.0)
    note_failed_probe_pending(extra, sign=1, bar=probe, prog={"class": "MARGINAL_EXTREME_ONLY", "renewed_directional_auction": False})
    assert extra.get("failed_probe_pending") is True
    note_failed_probe_pending(extra, sign=1, bar=opp, prog={"class": "NO_DIRECTIONAL_PROGRESS", "renewed_directional_auction": False})
    at_opp = classify_failed_break_reaccepted(sign=1, bar=opp, prev_bar=probe, extra=extra)
    assert at_opp.get("reaccepted") is False
    at_mid = classify_failed_break_reaccepted(sign=1, bar=mid, prev_bar=opp, extra=extra)
    assert at_mid.get("reaccepted") is False
    at_conf = classify_failed_break_reaccepted(sign=1, bar=conf, prev_bar=mid, extra=extra)
    assert at_conf.get("reaccepted") is True
    assert at_conf.get("reason") == "FAILED_BREAK_REACCEPTED"
    assert at_conf.get("family") == "AUCTION_ENDED_AS_RANGE"
    _ = at_mid
    real = _bar("09:45", "09:49", 3118.0, 3160.0, 3118.0, 3155.0)
    note_failed_probe_pending(extra, sign=1, bar=real, prog={"class": "REAL_BREAKOUT_EXTENSION", "renewed_directional_auction": True})
    assert extra.get("failed_probe_pending") is None


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

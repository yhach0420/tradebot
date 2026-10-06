"""Tests for native participation × S/R context discovery. No strategy. No Confirmation."""
from __future__ import annotations

import inspect

from research.native_participation_x_sr_context_discovery_v1 import (
    A2_REOPENED_AS_STRATEGY,
    C1_REOPENED,
    CASE_ADDS,
    CASE_FOUND,
    CASE_NO_INC,
    CASE_NONE,
    DETECTOR_RETUNED,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FAMILIES,
    FROZEN_VALIDATION_OPENED,
    OLD_CONFIRMATION_OPENED,
    PNL_OPTIMIZATION,
    REFRACTORY_BARS,
    TV_EXPAND_PCTL,
)
from research.native_participation_x_sr_context_discovery_v1.analyze import build_report_body, decide
from research.native_participation_x_sr_context_discovery_v1.isolation import OUT, write_overlap_n
from research.native_participation_x_sr_context_discovery_v1.match import find_same_symbol
from research.native_participation_x_sr_context_discovery_v1.native import ClockHistory, close_location, displacement, participation_expand
from research.native_participation_x_sr_context_discovery_v1.placebo import placebo_band, shift_sign
from research.native_participation_x_sr_context_discovery_v1.publish import SHEET_ORDER
from research.native_participation_x_sr_context_discovery_v1.spec import source_sha256
from research.native_participation_x_sr_context_discovery_v1.walk import _classify, walk_context
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256
from research.support_resistance_matched_separation_not_a_strategy_v1.inference import holm


def test_hashes_and_closed_paths():
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert DETECTOR_RETUNED is False
    assert A2_REOPENED_AS_STRATEGY is False
    assert C1_REOPENED is False
    assert PNL_OPTIMIZATION is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert TV_EXPAND_PCTL == 0.80
    assert REFRACTORY_BARS == 5
    assert FAMILIES == ("I1", "I2", "I3")
    assert source_sha256()
    assert len(source_sha256()) == 64


def test_no_tv_grid_or_strategy_economics():
    src = inspect.getsource(walk_context) + inspect.getsource(decide) + inspect.getsource(build_report_body)
    assert "0.70" not in inspect.getsource(participation_expand)
    assert "0.85" not in inspect.getsource(participation_expand)
    assert "0.90" not in inspect.getsource(participation_expand)
    assert "CAP" not in src
    assert "profit_factor" not in src.lower()
    assert "occupancy" not in src.lower()
    assert "c1_hold" not in inspect.getsource(walk_context)
    assert "a2_confirmed" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(walk_context)


def test_clock_prior_days_only():
    c = ClockHistory()
    for d in range(12):
        rec = {"t": ["10:00"], "va": [float(d)], "v": [float(d)]}
        p = c.tv_pctl("7203", "10:00", 100.0)
        assert p is None or d >= 10
        c.commit_day("7203", rec, [0])
    p = c.tv_pctl("7203", "10:00", 5.0)
    assert p is not None
    assert 0.0 <= p <= 1.0
    assert len(c.tv["7203"]["10:00"]) <= 20
    c2 = ClockHistory()
    rec = {"t": ["10:00"], "va": [1.0], "v": [1.0]}
    assert c2.tv_pctl("7203", "10:00", 1.0) is None
    c2.commit_day("7203", rec, [0])
    assert c2.tv_pctl("7203", "10:00", 1.0) is None


def test_displacement_and_expand():
    t = [f"09:{i:02d}" for i in range(30)]
    rec = {
        "t": t,
        "h": [101.0] * 25 + [103.0, 110.0, 110.0, 110.0, 110.0],
        "l": [99.0] * 30,
        "c": [100.0] * 25 + [102.0, 109.5, 109.5, 109.5, 109.5],
    }
    idx = list(range(30))
    assert close_location(110, 99, 109.5) >= 0.75
    assert participation_expand(0.80) is True
    assert participation_expand(0.79) is False
    d = displacement(rec, 26, idx, pos=26)
    assert d == "BULLISH"
    rec_b = {
        "t": t,
        "h": [101.0] * 30,
        "l": [99.0] * 25 + [97.0, 90.0, 90.0, 90.0, 90.0],
        "c": [100.0] * 25 + [98.0, 90.5, 90.5, 90.5, 90.5],
    }
    assert displacement(rec_b, 26, idx, pos=26) == "BEARISH"


def test_i3_requires_later_bar():
    src = inspect.getsource(_classify)
    assert "t > ht" in src
    hold = {
        "selection_slot": "NEAREST_ACTIVE_RESISTANCE_ABOVE",
        "as_resistance": True,
        "retest_hold": True,
        "retest_hold_time": "10:00",
        "i3_fired": False,
        "break": True,
        "first_test": True,
    }
    fam, ep = _classify([hold], "10:00", "BULLISH")
    assert fam is None
    fam, ep = _classify([hold], "10:01", "BULLISH")
    assert fam == "I3"


def test_i2_requires_native_in_walk():
    src = inspect.getsource(walk_context)
    assert "participation_expand(tv_p)" in src
    assert "forbidden_dates=conf | val" in src or "forbidden_dates=conf|val" in src.replace(" ", "")
    assert "native = bool(disp and participation_expand(tv_p))" in src


def test_i1_not_after_rejection():
    ep = {
        "selection_slot": "NEAREST_ACTIVE_SUPPORT_BELOW",
        "as_resistance": False,
        "first_test": True,
        "first_test_time": "09:30",
        "break": False,
        "rejection": True,
        "rejection_time": "09:40",
        "i1_fired": False,
    }
    fam, _ = _classify([dict(ep)], "09:50", "BULLISH")
    assert fam is None
    fam, _ = _classify([dict(ep)], "09:40", "BULLISH")
    assert fam == "I1"


def test_holm_three_families_only():
    adj = holm({"I1": 0.01, "I2": 0.04, "I3": 0.20})
    assert adj["m"] == 3
    assert set(adj["holm"]) == {"I1", "I2", "I3"}


def test_decide_four_verdicts():
    adv_yes = {"I1": {"advances": True}, "I2": {"advances": False}, "I3": {"advances": False}}
    adv_no = {"I1": {"advances": False}, "I2": {"advances": False}, "I3": {"advances": False}}
    d = decide({}, adv_yes, {"separates": False})
    assert d["VERDICT"] == CASE_FOUND
    assert d["NEXT"] == "SR_PARTICIPATION_MECHANISM_RCA_V1"
    d = decide({}, adv_yes, {"separates": True})
    assert d["VERDICT"] == CASE_ADDS
    d = decide({}, adv_no, {"separates": True})
    assert d["VERDICT"] == CASE_NO_INC
    d = decide({}, adv_no, {"separates": False})
    assert d["VERDICT"] == CASE_NONE


def test_match_ignores_event_bar_tv_and_range():
    src = inspect.getsource(find_same_symbol)
    assert "tv_clock_pctl" not in src
    assert "event_range" not in src
    assert "vol_clock_pctl" not in src


def test_placebo_discard_no_redraw():
    z = {"zone_id": "z1", "lo": 100.0, "hi": 101.0, "center": 100.5, "role": "RESISTANCE", "selection_slot": "NEAREST_ACTIVE_RESISTANCE_ABOVE", "selection_label": "ACTIVE_RESISTANCE"}
    occupied = [{"lo": 100.0, "hi": 101.0}]
    # shift will overlap the real zone or not depending on sign*atr; force overlap by large occupied
    occupied = [{"lo": -1e9, "hi": 1e9}]
    assert placebo_band(z, atr=1.0, symbol="7203", date="20240917", occupied=occupied) is None
    assert shift_sign("7203", "20240917") in (-1, 1)


def test_repair_placebo_semantics_not_demonstrated():
    from research.native_participation_x_sr_context_discovery_v1.analyze import repair_placebo_semantics

    report = {
        "decision": {"VERDICT": "NO_STABLE_NATIVE_PARTICIPATION_SR_INTERACTION_V1", "NEXT": "STOP_NATIVE_PARTICIPATION_SR_CONTEXT_V1"},
        "multiple_testing": {"holm": {"I1": 1.0}},
        "advance": {
            "I1": {"placebo_ok": True, "placebo_p20_gap": None},
            "I2": {"placebo_ok": True, "placebo_p20_gap": None},
            "I3": {"placebo_ok": True, "placebo_p20_gap": None},
        },
        "placebo": {
            "I1": {"d2_d4": {"matched_n": 0, "gap_p20_before_m20": None}},
            "I2": {"d2_d4": {"matched_n": 0, "gap_p20_before_m20": None}},
            "I3": {"d2_d4": {"matched_n": 0, "gap_p20_before_m20": None}},
        },
        "identity": {"native_event_n": 339436},
        "counts": {},
        "same_bar_entry_n": 0,
    }
    out = repair_placebo_semantics(report)
    ans = out["answers"]["Does S/R × participation show an interaction larger than placebo?"]
    assert ans == {"I1": "NOT_DEMONSTRATED", "I2": "NOT_DEMONSTRATED", "I3": "NOT_DEMONSTRATED"}
    assert out["advance"]["I1"]["placebo_ok"] is False
    assert out["advance"]["I1"]["placebo_status"] == "INSUFFICIENT_MATCHED_PLACEBO"
    assert out["decision"]["VERDICT"] == "NO_STABLE_NATIVE_PARTICIPATION_SR_INTERACTION_V1"
    assert out["decision"]["NEXT"] == "STOP_NATIVE_PARTICIPATION_SR_CONTEXT_V1"
    assert out["multiple_testing"]["holm"]["I1"] == 1.0


def test_placebo_ok_false_when_no_usable_contrast():
    from research.native_participation_x_sr_context_discovery_v1.analyze import _advance

    q = {
        "d2_d4": {
            "gap_p20_before_m20": 0.1,
            "gap_median_mfe": 0.05,
            "gap_p40_before_m20": 0.04,
            "matched_n": 80,
            "day_n": 20,
            "symbol_n": 10,
            "treatment": {"median_mfe": 50, "median_mae": -40},
            "matched_control": {"median_mfe": 40, "median_mae": -45},
        }
    }
    pb = {"d2_d4": {"gap_p20_before_m20": None, "matched_n": 0}}
    a = _advance(q, pb, [], 1.0)
    assert a["placebo_ok"] is False
    assert a["placebo_status"] == "INSUFFICIENT_MATCHED_PLACEBO"
    assert a["advances"] is False


def test_sheets_and_overlap():
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "I1_Result" in SHEET_ORDER
    assert "Multiple_Testing" in SHEET_ORDER
    assert OUT.name == "native_participation_x_sr_context_discovery_v1"

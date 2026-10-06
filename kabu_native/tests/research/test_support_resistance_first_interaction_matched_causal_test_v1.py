"""Tests for the matched causal S/R first-interaction test. No Confirmation. No Frozen Validation. No PnL."""
from __future__ import annotations

import inspect

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR, ZONE_HALF_ATR
from research.support_resistance_first_interaction_matched_causal_test_v1 import (
    CASE_NONE,
    CASE_SEP,
    DETECTOR_RETUNED,
    EXPECTED_FIRST_TEST_N,
    EXPECTED_PRIMARY_FIRST_TEST_N,
    FIVE_PP_CONTINUATION_SOLE_GATE,
    FROZEN_CONFIRM_ATR,
    FROZEN_VALIDATION_OPENED,
    FROZEN_ZONE_HALF_ATR,
    NEXT_NOT_STRATEGY,
    NEXT_STOP_NONE,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    PRIMARY_METRIC,
    X0_X1_USED_TO_SELECT_RULES,
)
from research.support_resistance_first_interaction_matched_causal_test_v1.analyze import decide
from research.support_resistance_first_interaction_matched_causal_test_v1.direction import is_primary, is_secondary, sign_for
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, freeze_record, state_machine_sha256
from research.support_resistance_first_interaction_matched_causal_test_v1.isolation import AUDIT_OUT, OUT, REBUILD_OUT, ZONE_OUT, write_overlap_n
from research.support_resistance_first_interaction_matched_causal_test_v1.match import find_control, placebo_zones
from research.support_resistance_first_interaction_matched_causal_test_v1.outcomes import signed_path
from research.support_resistance_first_interaction_matched_causal_test_v1.publish import SHEET_ORDER
from research.support_resistance_first_interaction_matched_causal_test_v1.spec import SOURCE_FILES, source_sha256
from research.support_resistance_first_interaction_matched_causal_test_v1.stats import decide_from_questions, pair_stats


def test_freeze_and_safety() -> None:
    assert PARENT_VERDICT == "SUPPORT_RESISTANCE_FACE_VALID_REBUILD_READY_V1"
    assert FROZEN_CONFIRM_ATR == CONFIRM_ATR == 0.75
    assert FROZEN_ZONE_HALF_ATR == ZONE_HALF_ATR == 0.15
    assert DETECTOR_RETUNED is False
    assert FIVE_PP_CONTINUATION_SOLE_GATE is False
    assert X0_X1_USED_TO_SELECT_RULES is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert PRIMARY_METRIC == "p20_before_m20"
    assert EXPECTED_PRIMARY_FIRST_TEST_N == 2713
    assert EXPECTED_FIRST_TEST_N == 6646
    fr = freeze_record()
    assert fr["retuned"] is False
    assert len(fr["DETECTOR_SHA256"]) == 64
    assert len(fr["STATE_MACHINE_SHA256"]) == 64
    assert detector_sha256() == fr["DETECTOR_SHA256"]
    assert state_machine_sha256() == fr["STATE_MACHINE_SHA256"]
    assert OUT.name == "support_resistance_first_interaction_matched_causal_test_v1"
    assert REBUILD_OUT.name == "support_resistance_face_valid_first_interaction_rebuild_v1"
    assert AUDIT_OUT.name == "support_resistance_test_design_audit_v1"
    assert ZONE_OUT.name == "multi_touch_daily_zone_1m_price_action_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Population_Primary" in SHEET_ORDER
    assert "Population_Secondary" in SHEET_ORDER
    assert len(source_sha256()) == 64
    assert "freeze.py" in SOURCE_FILES
    assert "machine.py" not in SOURCE_FILES


def test_primary_secondary_split() -> None:
    assert is_primary("NEAREST_ACTIVE_RESISTANCE_ABOVE")
    assert is_primary("NEAREST_ACTIVE_SUPPORT_BELOW")
    assert not is_primary("NEAREST_BROKEN_RESISTANCE_BELOW")
    assert not is_primary("NEAREST_BROKEN_SUPPORT_ABOVE")
    assert is_secondary("NEAREST_BROKEN_RESISTANCE_BELOW")
    assert is_secondary("NEAREST_BROKEN_SUPPORT_ABOVE")
    assert not is_secondary("NEAREST_ACTIVE_RESISTANCE_ABOVE")


def test_direction_signs() -> None:
    assert sign_for("A", resistance=True) == -1
    assert sign_for("A", resistance=False) == 1
    assert sign_for("B", resistance=True) == 1
    assert sign_for("B", resistance=False) == -1
    assert sign_for("C", resistance=True) == 1
    assert sign_for("C", resistance=False) == -1
    assert sign_for("D", resistance=True) == -1
    assert sign_for("D", resistance=False) == 1


def test_match_does_not_use_outcomes() -> None:
    src = inspect.getsource(find_control)
    for banned in ("p20", "p40", "p80", "mfe", "mae", "end_bps", "payoff", "fwd"):
        assert banned not in src
    tf = {
        "i": 10,
        "bucket": "0930",
        "r1": 0.001,
        "r3": 0.002,
        "r5": 0.003,
        "vol_rel": 1.0,
        "rng_rel": 1.0,
        "gap": "up",
        "mkt_sign": "up",
        "sec_sign": "up",
        "in_zone": True,
    }
    zone_bar = {**tf, "i": 11, "in_zone": True, "r5": 0.0031}
    ctrl = {
        "i": 12,
        "bucket": "0930",
        "r1": 0.001,
        "r3": 0.002,
        "r5": 0.0031,
        "vol_rel": 1.05,
        "rng_rel": 1.02,
        "gap": "up",
        "mkt_sign": "up",
        "sec_sign": "up",
        "in_zone": False,
    }
    got = find_control([tf, zone_bar, ctrl], tf)
    assert got is ctrl


def test_placebo_discards_overlap() -> None:
    prior = {"high": 110.0, "low": 90.0, "close": 100.0}
    active = [{"lo": 99.0, "hi": 101.0}]
    out = placebo_zones(prior=prior, atr=10.0, active=active, open_px=100.0)
    names = {z["selection_slot"] for z in out}
    assert "PLACEBO_MID" not in names
    assert "PLACEBO_PDC_UP" in names or "PLACEBO_PDC_DN" in names


def test_signed_path_resistance_rejection_is_down() -> None:
    rec = {
        "n": 4,
        "t": ["09:01", "09:02", "09:03", "09:04"],
        "o": [100.0, 100.0, 99.5, 99.0],
        "h": [100.2, 100.1, 99.6, 99.2],
        "l": [99.8, 99.4, 98.8, 98.5],
        "c": [100.0, 99.5, 99.0, 98.8],
    }
    down = signed_path(rec, 0, -1)
    up = signed_path(rec, 0, 1)
    assert down["end_bps"] is not None and down["end_bps"] > 0
    assert up["end_bps"] is not None and up["end_bps"] < 0


def test_decide_separation_is_not_a_strategy() -> None:
    q = {
        "A": {"powered": True, "separation_on_primary_metric": True},
        "B": {"powered": True, "separation_on_primary_metric": False},
        "C": {"powered": False, "separation_on_primary_metric": False},
        "D": {"powered": False, "separation_on_primary_metric": False},
    }
    d = decide(bind_ok=True, freeze_ok=True, identity_ok=True, mixed=False, qs=q)
    assert d["VERDICT"] == CASE_SEP
    assert d["NEXT"] == NEXT_NOT_STRATEGY
    assert d["is_strategy"] is False
    none = decide(
        bind_ok=True,
        freeze_ok=True,
        identity_ok=True,
        mixed=False,
        qs={k: {"powered": True, "separation_on_primary_metric": False} for k in "ABCD"},
    )
    assert none["VERDICT"] == CASE_NONE
    assert none["NEXT"] == NEXT_STOP_NONE
    sec = decide_from_questions(q)
    assert sec["secondary_must_not_influence_primary"] is True
    mixed = decide(bind_ok=True, freeze_ok=True, identity_ok=True, mixed=True, qs=q)
    assert mixed["VERDICT"] != CASE_SEP


def test_pair_stats_gap_uses_matched_pairs_only() -> None:
    rows = [
        {"matched": True, "date": "d1", "symbol": "s1", "tr_p20_before_m20": True, "ct_p20_before_m20": False},
        {"matched": False, "date": "d1", "symbol": "s1", "tr_p20_before_m20": True},
    ]
    st = pair_stats(rows)
    assert st["treatment_n"] == 2
    assert st["matched_n"] == 1
    assert st["treatment"]["n"] == 1
    assert st["treatment_all"]["n"] == 2
    assert st["gap_p20_before_m20"] == 1.0
    assert st["causal_contrast_on_matched_pairs_only"] is True

"""Tests for A2 bracketed complete strategy. C1 closed. Confirmation closed. No detector retune."""
from __future__ import annotations

import inspect

from research.sr_a2_bracketed_structure_complete_strategy_v1 import (
    C1_REOPENED,
    CASE_FAIL,
    DETECTOR_RETUNED,
    EXPECTED_DETECTOR_SHA256,
    EXPECTED_STATE_MACHINE_SHA256,
    FROZEN_VALIDATION_OPENED,
    OLD_CONFIRMATION_OPENED,
    PARENT_VERDICT,
    POST_HOC_DEVELOPMENT_ARCHITECTURE,
    PNL_BASED_RULE_CHANGE,
    STRATEGY_ID,
)
from research.sr_a2_bracketed_structure_complete_strategy_v1.analyze import decide
from research.sr_a2_bracketed_structure_complete_strategy_v1.eligibility import opposing_at_decision
from research.sr_a2_bracketed_structure_complete_strategy_v1.isolation import OUT, PARENT_OUT, write_overlap_n
from research.sr_a2_bracketed_structure_complete_strategy_v1.publish import SHEET_ORDER
from research.sr_a2_bracketed_structure_complete_strategy_v1.spec import source_sha256
from research.sr_a2_bracketed_structure_complete_strategy_v1.walk import walk_bracketed
from research.support_resistance_first_interaction_matched_causal_test_v1.freeze import detector_sha256, state_machine_sha256


def test_hashes_and_post_hoc_status():
    assert detector_sha256() == EXPECTED_DETECTOR_SHA256
    assert state_machine_sha256() == EXPECTED_STATE_MACHINE_SHA256
    assert DETECTOR_RETUNED is False
    assert POST_HOC_DEVELOPMENT_ARCHITECTURE is True
    assert C1_REOPENED is False
    assert OLD_CONFIRMATION_OPENED is False
    assert FROZEN_VALIDATION_OPENED is False
    assert PNL_BASED_RULE_CHANGE is False
    assert PARENT_VERDICT == "SR_PATH_SEPARATION_NOT_COMPLETE_STRATEGY_V1"
    assert STRATEGY_ID == "SR_A2_BRACKETED_COMPLETE_V1"
    assert source_sha256()


def test_c1_not_in_walk():
    src = inspect.getsource(walk_bracketed)
    assert "c1_hold" not in src
    assert "retest_hold" not in src
    assert "tod_min" in inspect.getsource(walk_bracketed)  # covariate for confound, not a filter
    assert "volume threshold" not in src
    assert "LONG-only" not in src


def test_eligibility_uses_decision_close_not_entry_open():
    src = inspect.getsource(walk_bracketed)
    assert "decision_px = float(rec[\"c\"][int(di_i)])" in src
    assert "used_entry_open_for_eligibility" in src
    zones = [{"zone_id": "r1", "lo": 110.0, "hi": 112.0, "center": 111.0, "ZONE_ACTIVATED_AT": "20240916"}]
    hit = opposing_at_decision(
        side="LONG",
        decision_px=100.0,
        entry_zone_id="sup",
        session_date="20240917",
        resistance_active=zones,
        support_active=[],
    )
    assert hit["opposing_zone_available"] is True
    assert hit["used_entry_open"] is False
    assert hit["used_future_bar"] is False
    assert hit["target_price"] == 110.0
    miss = opposing_at_decision(
        side="LONG",
        decision_px=120.0,
        entry_zone_id="sup",
        session_date="20240917",
        resistance_active=zones,
        support_active=[],
    )
    assert miss["opposing_zone_available"] is False


def test_future_zone_not_eligible():
    hit = opposing_at_decision(
        side="LONG",
        decision_px=100.0,
        entry_zone_id="z",
        session_date="20240917",
        resistance_active=[{"zone_id": "f", "lo": 110.0, "hi": 111.0, "center": 110.5, "ZONE_ACTIVATED_AT": "20240918"}],
        support_active=[],
    )
    assert hit["opposing_zone_available"] is False
    assert hit["used_entry_open"] is False


def test_decide_fails_if_d2_fails():
    def pack(stress, pf, blocks, top_sym=10, best_day=10, bps=5.0):
        return {
            "economics": {
                "stress_yen": stress,
                "pf_stress": pf,
                "mean_stress_bps": bps,
                "target_exit_rate": 0.02,
                "session_close_n": 80,
                "trades": 100,
                "blocks": blocks,
                "concentration": {
                    "top_symbol_removed": {"stress_yen": top_sym},
                    "best_day_removed": {"stress_yen": best_day},
                },
            }
        }

    blocks_bad = {
        "D2": {"n": 10, "stress_yen": -1, "pf_stress": 0.9},
        "D3": {"n": 10, "stress_yen": 10, "pf_stress": 1.2},
        "D4": {"n": 10, "stress_yen": 10, "pf_stress": 1.2},
    }
    walked = {"TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N": 0, "TARGET_ELIGIBILITY_FUTURE_BAR_N": 0, "same_bar_entry_n": 0, "target_future_leakage_n": 0}
    d = decide(
        walked=walked,
        primary=pack(100, 1.2, blocks_bad),
        reverse=pack(90, 1.1, blocks_bad),
        hashed=pack(80, 1.1, blocks_bad),
        confound={"notional_confound": False},
    )
    assert d["VERDICT"] == CASE_FAIL
    assert d["NEXT"] == "STOP_SUPPORT_RESISTANCE_STRATEGY_PATH_V1"
    assert d["freeze"] is False


def test_tie_sign_flip_cannot_freeze():
    blocks = {
        "D2": {"n": 10, "stress_yen": 10, "pf_stress": 1.2},
        "D3": {"n": 10, "stress_yen": 10, "pf_stress": 1.2},
        "D4": {"n": 10, "stress_yen": 10, "pf_stress": 1.2},
    }
    walked = {"TARGET_ELIGIBILITY_USES_ENTRY_OPEN_N": 0, "TARGET_ELIGIBILITY_FUTURE_BAR_N": 0, "same_bar_entry_n": 0, "target_future_leakage_n": 0}

    def pack(stress):
        return {
            "economics": {
                "stress_yen": stress,
                "pf_stress": 1.2 if stress > 0 else 0.8,
                "mean_stress_bps": 4.0 if stress > 0 else -1.0,
                "target_exit_rate": 0.2,
                "session_close_n": 10,
                "trades": 100,
                "blocks": blocks,
                "concentration": {"top_symbol_removed": {"stress_yen": 5}, "best_day_removed": {"stress_yen": 5}},
            }
        }

    d = decide(walked=walked, primary=pack(100), reverse=pack(-10), hashed=pack(50), confound={"notional_confound": False})
    assert d["CAP_TIE_ORDER_FRAGILE"] is True
    assert d["VERDICT"] == CASE_FAIL


def test_sheets_and_overlap():
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Eligibility_Timestamp_Audit" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    assert OUT.name == "sr_a2_bracketed_structure_complete_strategy_v1"
    assert PARENT_OUT.name == "support_resistance_mechanism_to_complete_strategy_v1"

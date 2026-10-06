"""Existing architecture exhaustion review. Inventory only. No harvest."""
from __future__ import annotations

from research.existing_architecture_exhaustion_review_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    NEXT_OR_RECONCILE,
    NEXT_STATE_SPACE,
    STATUS_ENUM,
)
from research.existing_architecture_exhaustion_review_v1.analyze import (
    build_answers,
    decide,
    priority_key,
    remaining_eligible,
)
from research.existing_architecture_exhaustion_review_v1.inventory import architecture_inventory
from research.existing_architecture_exhaustion_review_v1.spec import canonical_spec


def test_status_enum_frozen_and_no_new_architecture():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID == "EXISTING_ARCHITECTURE_EXHAUSTION_REVIEW_V1"
    assert spec["NEW_ENTRY"] is False
    assert spec["NEW_EXIT"] is False
    assert spec["NEW_REPLAY"] is False
    assert spec["NEW_PNL_SIMULATION"] is False
    assert spec["STRESS_OPEN"] is False
    assert spec["STANDALONE_OPEN_STRENGTH"] is False
    assert spec["PNL_USED_FOR_NEXT"] is False
    assert spec["OPEN_STRENGTH_STANDALONE_CLASSIFICATION"] == "NEW_DERIVATIVE_NOT_PREEXISTING"
    assert tuple(spec["STATUS_ENUM"]) == STATUS_ENUM


def test_or_standalone_is_new_derivative_and_or_overlay_is_partial():
    by = {r["ARCHITECTURE_ID"]: r for r in architecture_inventory()}
    stand = by["OPEN_STRENGTH_STANDALONE"]
    overlay = by["PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"]
    assert stand["CURRENT_STATUS"] == "NEW_DERIVATIVE_NOT_PREEXISTING"
    assert stand["PREEXISTING"] is False
    assert remaining_eligible(stand) is False
    assert overlay["CURRENT_STATUS"] == "PREEXISTING_PARTIALLY_TESTED"
    assert overlay["ORIGINAL_CAP"] == "cap_or=1"
    assert overlay["ACTUAL_PAPER_RUN"] is True
    assert overlay["FULL_CAUSAL_RUN"] is False
    assert remaining_eligible(overlay) is True


def test_closed_families_not_remaining():
    by = {r["ARCHITECTURE_ID"]: r for r in architecture_inventory()}
    closed = [
        "E1_X6R3_CONT_PULL_BREAK",
        "E1_X6_SCORE_JOINT",
        "E1_X6_FCRR",
        "E1_X6_TAER",
        "RPFE",
        "VCIE",
        "SIMPLE_TECH_ENTRY_FAMILY",
        "SIMPLE_TECH_BRANCH_U",
        "SIMPLE_TECH_BRANCH_P",
        "AM_C0",
        "V1R_P1",
        "BREAKOUT_CONTINUATION",
        "VWAP_REJECTION_RECLAIM",
        "FAILED_BREAKDOWN_RECLAIM",
        "E4_X2_Z3",
        "RECOVERY_SEQUENCE",
        "PARTICIPATION_ONSET",
        "DYNAMIC_ANCHOR_TRAIL10",
        "SIMPLE_FULL_STRATEGY_75_GRID",
    ]
    for aid in closed:
        assert remaining_eligible(by[aid]) is False, aid
    assert by["E4_X2_Z3"]["CURRENT_STATUS"] == "CLOSED_ECONOMIC_FAILURE"
    assert by["RECOVERY_SEQUENCE"]["CURRENT_STATUS"] == "CLOSED_ECONOMIC_FAILURE"
    assert by["PARTICIPATION_ONSET"]["CURRENT_STATUS"] == "CLOSED_ECONOMIC_FAILURE"
    assert by["VCIE"]["CURRENT_STATUS"] == "CLOSED_INTEGRITY_FAILURE"
    assert by["RPFE"]["CURRENT_STATUS"] == "CLOSED_ECONOMIC_FAILURE"
    assert by["AM_C0"]["STRESS_INFORMED"] is True
    assert all(r["FUTURE_USED"] is False for r in by.values())
    assert all(r["CURRENT_STATUS"] in STATUS_ENUM for r in by.values())


def test_fixed_priority_selects_or_overlay_not_pnl():
    pack = decide()
    assert pack["CASE_NAME"] == CASE_A
    assert pack["VERDICT"] == CASE_A
    assert pack["NEXT"] == NEXT_OR_RECONCILE
    assert pack["SELECTED_NEXT_ARCHITECTURE"] == "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"
    assert pack["PNL_USED_TO_SELECT_NEXT"] is False
    assert pack["OR_SLEEVE_ISOLATED_FULL_CAUSAL_ALREADY_RUN"] is False
    remaining = pack["remaining_eligible_ids"]
    assert remaining[0] == "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"
    assert "E1_X7_PFQ" in remaining
    or_row = next(r for r in pack["inventory"] if r["ARCHITECTURE_ID"] == "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY")
    x7 = next(r for r in pack["inventory"] if r["ARCHITECTURE_ID"] == "E1_X7_PFQ")
    assert priority_key(or_row) < priority_key(x7)
    assert CASE_B and CASE_C and NEXT_STATE_SPACE
    answers = build_answers(pack)
    assert answers["5_production_or_classic_opening_range_breakout"] is False
    assert answers["6_standalone_open_strength_preexisting"] is False
    assert answers["8_or_sleeve_isolated_full_causal_already_run"] is False
    assert answers["29_pnl_used_to_select_next"] is False
    assert answers["31_new_economics_run"] is False
    assert answers["32_stress_raw_read"] is False
    assert answers["35_max_new_data_date"] == "NONE"
    assert answers["37_submit_cancel_live"] == "0/0/0"
    assert answers["25_insufficient_classification_n"] == 0
    assert answers["24_preexisting_untested_full_causal_n"] == 0
    assert pack["counts"]["INSUFFICIENT_CLASSIFICATION_N"] == 0
    assert pack["open_strength_audit"]["CLASSIC_OPENING_RANGE_BREAKOUT"] is False
    assert pack["x6r3_audit"]["REOPEN_X6R3"] is False

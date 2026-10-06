"""NEW_ARCHITECTURE_CLASS_RETHINK_V1. Class selection only. No rules. No event-count ranking."""
from __future__ import annotations

from research.new_architecture_class_rethink_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CLASS_IDS,
    ELIGIBILITY_FIELDS,
    NEXT_IF_B,
    PFQ_RECON_FORBIDDEN,
)
from research.new_architecture_class_rethink_v1.analyze import build_answers, decide
from research.new_architecture_class_rethink_v1.classes import priority_key, select_class
from research.new_architecture_class_rethink_v1.feasibility import htf_completed_asof_proof
from research.new_architecture_class_rethink_v1.spec import canonical_spec


def test_contract_bans_rules_economics_and_event_counts():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["ARCHITECTURE_CLASSES"] == list(CLASS_IDS)
    assert spec["FIFTH_CLASS_FORBIDDEN"] is True
    assert spec["COMPOSITE_ARCHITECTURE_EVENT_COUNT_N"] == 0
    assert spec["NEW_TRIGGER_DEFINITION_N"] == 0
    assert spec["NEW_THRESHOLD_DEFINITION_N"] == 0
    assert spec["PNL_USED_TO_SELECT_CLASS"] is False
    assert spec["EVENT_COUNT_USED_TO_SELECT_CLASS"] is False
    assert spec["EXACT_ENTRY_RULE_CREATION"] is False
    assert spec["EXACT_EXIT_RULE_CREATION"] is False
    assert spec["CANDIDATE_LIBRARY_GENERATION"] is False
    assert spec["THRESHOLD_SELECTION"] is False
    assert spec["TIMEFRAME_SELECTION_BY_ECONOMICS"] is False
    assert spec["AGE_THRESHOLD_SELECTION"] is False
    assert spec["CROWDING_THRESHOLD_SELECTION"] is False
    assert spec["LABEL_GUIDED_FEATURE_SELECTION"] is False
    assert spec["ECONOMICS_RUN"] is False
    assert spec["PRIOR_84_GRID_EXECUTED"] is False
    assert spec["SIZING"] is False
    assert spec["STRESS_OPEN"] is False
    assert spec["FUTURE_DATA"] is False
    assert spec["PFQ_REVIVAL"] is False
    assert spec["OR_ECONOMICS_OPENED"] is False
    assert spec["NEW_MARKET_DATA_AFTER_20260807_ALLOWED"] is False
    assert spec["NEXT_IF_B"] == NEXT_IF_B == "RESEARCH_OBJECTIVE_REBASE_V1"
    assert spec["PFQ_RECON_FORBIDDEN"] == PFQ_RECON_FORBIDDEN


def test_htf_asof_rejects_mid_bucket_carryback():
    proof = htf_completed_asof_proof()
    assert proof["ok"] is True
    assert proof["ASOF_AT_1M_T0_120_MID_BUCKET"] is None
    assert proof["SAME_BUCKET_CARRYBACK_REJECTED"] is True
    assert abs(float(proof["FIRST_3M_FINALIZE_T"]) - 180.0) < 1e-12
    assert abs(float(proof["ASOF_AT_1M_T0_180_COMPLETED"]) - 180.0) < 1e-12
    assert proof["V7_SAME_BUCKET_DIAGNOSTIC_JOIN"] == "REJECTED_FOR_C1"


def test_decide_no_fifth_class_no_counts_in_priority():
    pack = decide()
    rows = list(pack["eligibility"])
    assert [r["CLASS_ID"] for r in rows] == list(CLASS_IDS)
    assert pack["FIFTH_CLASS_CREATED"] is False
    assert pack["COMPOSITE_ARCHITECTURE_EVENT_COUNT_N"] == 0
    assert pack["NEW_TRIGGER_DEFINITION_N"] == 0
    assert pack["NEW_THRESHOLD_DEFINITION_N"] == 0
    assert pack["CANDIDATE_LIBRARY_GENERATED"] is False
    assert pack["PNL_USED_TO_SELECT_CLASS"] is False
    assert pack["EVENT_COUNT_USED_TO_SELECT_CLASS"] is False
    assert pack["causality"]["FUTURE_EPISODE_ENDPOINT_USED"] is False
    assert pack["causality"]["FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER"] is False
    assert pack["causality"]["LABEL_GUIDED_FEATURE_SELECTION"] is False
    for r in rows:
        assert tuple(r.keys()) == ELIGIBILITY_FIELDS
        assert "ESTIMATED_EVENT_N" not in r
        key = priority_key(r)
        assert len(key) == 8
        assert not any(isinstance(x, int) and x > 20 for x in key[:-1])
    c2 = next(r for r in rows if r["CLASS_ID"] == "C2_EPISODE_AGE")
    assert c2["ELIGIBLE"] is False
    assert c2["CLOSED_LINEAGE_MATCH"] is True
    c3 = next(r for r in rows if r["CLASS_ID"] == "C3_FAILURE_ROUTING")
    assert c3["ELIGIBLE"] is False
    assert c3["LABEL_LEAKAGE_REQUIRED"] is False
    answers = build_answers(pack)
    assert answers["51_COMPOSITE_ARCHITECTURE_EVENT_COUNT_N"] == 0
    assert answers["52_NEW_TRIGGER_DEFINITION_N"] == 0
    assert answers["53_NEW_THRESHOLD_DEFINITION_N"] == 0
    assert answers["54_EVENT_COUNT_USED_TO_SELECT_CLASS"] is False
    assert answers["57_C2_HINDSIGHT_ENDPOINT_USED"] is False
    assert answers["58_C3_FUTURE_OUTCOME_LABEL_USED"] is False
    assert answers["59_C3_LABEL_GUIDED_FEATURE_SELECTION"] is False
    assert answers["62_NEW_MARKET_DATA_AFTER_20260807_READ"] is False
    assert answers["63_CANDIDATE_LIBRARY_GENERATED"] is False
    assert answers["2_or_economics_opened"] is False
    assert answers["4_pfq_revival_allowed"] is False
    assert answers["6_EXISTING_ARCHITECTURE_EXHAUSTED"] is True
    assert answers["43_Runtime_changed"] is False
    assert answers["44_submit_cancel_live"] == "0/0/0"
    assert answers["45_TRUE_OOS"] is False
    assert answers["46_CERTIFIED"] is False
    eligible_n = sum(1 for r in rows if r["ELIGIBLE"])
    if eligible_n >= 1:
        assert pack["CASE_NAME"] == CASE_A
        assert pack["SELECTED_ARCHITECTURE_CLASS"] in CLASS_IDS
        assert pack["NEXT"] == f"{pack['SELECTED_ARCHITECTURE_CLASS']}_PRECOMMIT_V1"
        assert pack["NEXT"] != PFQ_RECON_FORBIDDEN
        assert select_class(rows)["CLASS_ID"] == pack["SELECTED_ARCHITECTURE_CLASS"]
    else:
        assert pack["CASE_NAME"] == CASE_B
        assert pack["NEXT"] == NEXT_IF_B
        assert pack["SELECTED_ARCHITECTURE_CLASS"] is None
    # Event counts exist as diagnostics only and are absent from the priority key.
    assert "used_to_select_class" in answers["49_primitive_coverage_diagnostics"]
    assert answers["49_primitive_coverage_diagnostics"]["used_to_select_class"] is False
    assert answers["47_VERDICT"] == pack["VERDICT"]
    assert answers["48_NEXT"] == pack["NEXT"]
    assert answers["55_C1_HIGHER_TF_COMPLETED_SEMANTICS_PROVEN"] is pack["causality"]["HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN"]
    assert answers["56_C2_CAUSAL_EPISODE_ONSET_OBSERVABLE"] is True
    assert answers["60_C4_PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE"] is pack["causality"]["PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE"]

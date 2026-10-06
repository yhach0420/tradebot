"""Pause decision. No strategy. No economics. No date assignment."""
from __future__ import annotations

from typing import Any

from research.research_pause_and_data_requirements_redesign_v1 import (
    CASE_B,
    INTERNAL_FOLD_N,
    NEW_DEV_MIN_CALENDAR_WEEKS,
    NEW_DEV_MIN_VALID_DAYS,
    NEXT_B,
    REQUIRED_CLOSED_ARCHITECTURE_N,
    REQUIRED_ELIGIBLE_METHOD_N,
    TRUE_OOS_MIN_VALID_DAYS,
)
from research.research_pause_and_data_requirements_redesign_v1.requirements import (
    burned_data,
    current_information_boundary,
    current_method_boundary,
    d1_d10_for_local_unused,
    dataset_retirement,
    future_dev_protocol,
    future_method_requirements,
    future_oos_protocol,
    missing_information_categories,
    restart_gates,
)
from research.research_pause_and_data_requirements_redesign_v1.spec import freeze_protocol


def meta_overfit() -> dict[str, Any]:
    return {
        "CURRENT_DEV_META_RESEARCH_BURNED": True,
        "HISTORICAL_RESULTS_INVALID": False,
        "MEANING": (
            "A newly invented strategy that now succeeds on the same 10 LEGACY_DEV days "
            "cannot establish credible discovery evidence."
        ),
        "FRESH_INDEPENDENT_DEVELOPMENT_SAMPLE_REQUIRED": True,
        "FRESH_DATA_ONLY_RESTART_ALLOWED": False,
    }


def decide(missing: list[dict[str, Any]]) -> dict[str, Any]:
    local_unused = [
        r
        for r in missing
        if r.get("CURRENTLY_CAPTURED") is False
        and r.get("DERIVABLE_FROM_CURRENT_CAPTURE") is False
        and r.get("MATERIAL_INFORMATION_GAIN") is True
        and r.get("HISTORICAL_REPLAY_POSSIBLE") is True
        and r.get("RUNTIME_AVAILABILITY_KNOWN") is True
    ]
    d_gates = d1_d10_for_local_unused()
    case_a = bool(local_unused) and bool(d_gates.get("ALL_PASS"))
    gates = restart_gates(rg3=False, rg4=False)
    # CASE A requires a locally available unused raw category. None exists.
    # CASE D would require that no restart path can even be defined. A path is defined:
    # new external information policy + frozen METH1-10 method + unread mechanical NEW_DEV
    # + explicit future-use policy. None of that is authorized now.
    # CASE C would treat fresh data + a new method on the same Capture domain as sufficient.
    # That is forbidden: FRESH_DATA_ONLY_RESTART_ALLOWED=false and NEW_CAUSAL_INFORMATION_REQUIRED=true.
    # CASE B: no locally available unused causal category; a new external source would be required.
    assert case_a is False
    return {
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": NEXT_B,
        "LOCALLY_AVAILABLE_UNUSED_RAW_CATEGORY": False,
        "SELECTED_INFORMATION_CATEGORY_ID": None,
        "NEW_CAUSAL_INFORMATION_REQUIRED": True,
        "FRESH_INDEPENDENT_DEVELOPMENT_SAMPLE_REQUIRED": True,
        "FRESH_DATA_ONLY_RESTART_ALLOWED": False,
        "FUTURE_USE_REQUIRES_EXPLICIT_POLICY_CHANGE": True,
        "STRATEGY_RESEARCH_STATUS": "PAUSED",
        "RESTART_ALLOWED": bool(gates["RESTART_ALLOWED"]),
        "NEW_INFORMATION_OBJECT_SPACE_EXHAUSTED": True,
        "JUSTIFIED_ARCHITECTURE_SPACE_EXHAUSTED": True,
        "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": True,
        "CURRENT_CAPTURE_INFORMATION_DOMAIN_CLOSED": True,
        "CURRENT_DEV_META_RESEARCH_BURNED": True,
        "LEGACY_DEV_RESEARCH_BURNED": True,
        "LEGACY_DEV_ALLOWED_FOR_NEW_STRATEGY_SELECTION": False,
        "ENTRY_ONLY_METHOD_REVIVAL": False,
        "NEW_EXTERNAL_DATA_ACQUIRED": False,
        "FUTURE_DATASET_DATES_ASSIGNED": False,
        "NEW_STRATEGY_CREATED": False,
        "NEW_ENTRY_CREATED": False,
        "NEW_EXIT_CREATED": False,
        "NEW_MODEL_CREATED": False,
        "NEW_THRESHOLD_CREATED": False,
        "NEW_CANDIDATE_LIBRARY_CREATED": False,
        "NEW_ECONOMIC_RUN": False,
        "NEW_OUTCOME_COMPUTE": False,
        "INTERPRETATION": (
            "Strategy discovery is exhausted under the current sealed development dataset, "
            "current captured information domain, and currently justified research methods. "
            "Any restart requires a clean precommitted research reset. More days of the same "
            "data do not automatically solve the problem. A new feature is not automatically "
            "required; a raw causal source not reconstructable from the 13-object domain is. "
            "This does not say the market has no exploitable edge, that more data guarantees "
            "success, or that ML is impossible."
        ),
        **gates,
    }


def build_report_body(*, parent: dict[str, Any]) -> dict[str, Any]:
    missing = missing_information_categories()
    decision = decide(missing)
    protocol = freeze_protocol(
        {
            "dataset_retirement": dataset_retirement(),
            "burned_data": burned_data(),
            "future_method_requirements": future_method_requirements(),
            "future_dev_protocol": future_dev_protocol(),
            "future_oos_protocol": future_oos_protocol(),
        }
    )
    return {
        "parent": parent,
        "dataset_retirement": dataset_retirement(),
        "burned_data": burned_data(),
        "current_information_boundary": current_information_boundary(),
        "current_method_boundary": current_method_boundary(),
        "meta_overfit": meta_overfit(),
        "missing_information": missing,
        "d1_d10": d1_d10_for_local_unused(),
        "future_method_requirements": future_method_requirements(),
        "future_dev_protocol": future_dev_protocol(),
        "future_oos_protocol": future_oos_protocol(),
        "restart_gate": restart_gates(rg3=False, rg4=False),
        "freeze": {
            "PAUSE_PROTOCOL_SPEC_SHA256": protocol["PAUSE_PROTOCOL_SPEC_SHA256"],
            "CLOSED_ARCHITECTURE_FAMILY_N": REQUIRED_CLOSED_ARCHITECTURE_N,
            "ELIGIBLE_RESEARCH_METHOD_N": REQUIRED_ELIGIBLE_METHOD_N,
            "SELECTED_METHOD_ID": None,
            "SELECTED_INFORMATION_CATEGORY_ID": None,
        },
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    d = dict(report.get("decision") or {})
    missing = list(report.get("missing_information") or [])
    ids = [str(r["CATEGORY_ID"]) for r in missing]
    gates = dict(report.get("restart_gate") or {})
    return {
        "1_parent_verdict_pinned": bool(parent.get("ok")),
        "2_remaining_eligible_prior_method_N": int(parent.get("ELIGIBLE_RESEARCH_METHOD_N") or 0),
        "3_closed_architecture_family_N": int(parent.get("CLOSED_ARCHITECTURE_FAMILY_N") or 0),
        "4_LEGACY_DEV_RESEARCH_BURNED": True,
        "5_legacy_DEV_usable_for_new_strategy_selection": False,
        "6_burned_Holdout_promoted_to_new_DEV": False,
        "7_burned_Stress_promoted_to_OOS": False,
        "8_20260903_04_quarantine_changed": False,
        "9_20260907_plus_read": False,
        "10_20260907_plus_assigned_to_NEW_DEV": False,
        "11_current_Capture_information_domain_closed": True,
        "12_current_justified_method_space_closed": True,
        "13_current_DEV_meta_research_burned": True,
        "14_fresh_independent_development_data_required": True,
        "15_fresh_data_alone_sufficient": False,
        "16_new_causal_information_required": True,
        "17_missing_information_category_N": len(missing),
        "18_missing_information_category_IDs": ids,
        "19_selected_recommended_information_category_ID": None,
        "20_derivable_from_current_Capture": False,
        "21_causal_timestamp_possible": False,
        "22_historical_replay_possible": False,
        "23_runtime_availability_known": False,
        "24_new_external_data_acquired": False,
        "25_future_method_must_be_bounded": True,
        "26_Full_Causal_selection_unit_mandatory": True,
        "27_explainable_frozen_strategy_mandatory": True,
        "28_NEW_DEV_MIN_VALID_DAYS": int(NEW_DEV_MIN_VALID_DAYS),
        "29_NEW_DEV_MIN_CALENDAR_WEEKS": int(NEW_DEV_MIN_CALENDAR_WEEKS),
        "30_chronological_internal_folds_N": int(INTERNAL_FOLD_N),
        "31_TRUE_OOS_MIN_VALID_DAYS": int(TRUE_OOS_MIN_VALID_DAYS),
        "32_future_dataset_dates_assigned": False,
        "33_future_use_requires_explicit_policy_change": True,
        "34_RG1": bool(gates.get("RG1_LEGACY_DEV_retired")),
        "35_RG2": bool(gates.get("RG2_closed_architectures_remain_closed")),
        "36_RG3": bool(gates.get("RG3_new_causal_information_or_justified_premise")),
        "37_RG4": bool(gates.get("RG4_method_capacity_frozen_before_outcomes")),
        "38_RG5": bool(gates.get("RG5_NEW_DEV_date_selection_rule_frozen")),
        "39_RG6": bool(gates.get("RG6_NEW_DEV_unread_until_above_frozen")),
        "40_RG7": bool(gates.get("RG7_future_use_policy_explicitly_permits")),
        "41_RESTART_ALLOWED": bool(d.get("RESTART_ALLOWED")),
        "42_new_strategy_created": False,
        "43_new_ENTRY_created": False,
        "44_new_EXIT_created": False,
        "45_new_model_created": False,
        "46_candidate_library_created": False,
        "47_new_economic_run": False,
        "48_new_outcome_computed": False,
        "49_Holdout_read": False,
        "50_Stress_read": False,
        "51_future_read": False,
        "52_Sizing": False,
        "53_Runtime_changed": False,
        "54_Capture_changed": False,
        "55_submit_cancel_live": "0/0/0",
        "56_TRUE_OOS": False,
        "57_CERTIFIED": False,
        "58_VERDICT": d.get("VERDICT"),
        "59_NEXT": d.get("NEXT"),
    }

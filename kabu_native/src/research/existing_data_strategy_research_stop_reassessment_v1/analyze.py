"""Eligibility gates and CASE A/B. No strategy design. No PnL."""
from __future__ import annotations

from typing import Any

from research.existing_data_strategy_research_stop_reassessment_v1 import (
    BOUNDED_DATA_DRIVEN_ID,
    CASE_A,
    CASE_B,
    NEXT_A,
    NEXT_B,
)
from research.existing_data_strategy_research_stop_reassessment_v1.inventory import (
    architecture_inventory,
    e1_model_audit,
    joint_full_strategy_audit,
    method_taxonomy,
)
from research.existing_data_strategy_research_stop_reassessment_v1.spec import freeze_method_audit


def bounded_data_driven_eligibility() -> dict[str, Any]:
    """Prove not already equivalent, then apply R1-R12. Do not auto-revive ML."""
    joint = joint_full_strategy_audit()
    gates = {
        "R1_existing_sealed_DEV_only": True,
        "R2_no_new_information_source": True,
        "R3_materially_distinct_from_executed": True,
        "R4_TRAIN_only_candidate_formation_possible": True,
        "R5_Complete_Full_Causal_eval_possible": True,
        "R6_EXIT_CAP_occupancy_slot_before_selection_possible": True,
        "R7_no_Holdout_Stress_for_construction": True,
        "R8_does_not_reopen_exact_closed_strategy": True,
        "R9_capacity_bounded_before_outcomes": False,
        "R10_no_unlimited_threshold_feature_grid": False,
        "R11_internal_DEV_temporal_validation_possible": True,
        "R12_explainable_frozen_strategy_not_score_only": False,
    }
    why = (
        "BOUNDED_DATA_DRIVEN_FULL_CAUSAL_STRATEGY_LEARNING was not executed in equivalent form: "
        "H5 discovery used raw-horizon labels, and Full Causal runs used prewritten libraries "
        f"({', '.join(joint['PREWRITTEN_IDS'][:4])}…). "
        "It is still ineligible. A bounded generator from existing sealed information and the "
        "already-inventoried 1-minute / recovery / FDG vocabularies collapses to a finite library "
        "already scored under Complete Full Causal (R3 fails if treated as that library; "
        "FCMD BASE_QUALIFIED_N=0). Expanding definitions without a prewritten bound requires "
        "unlimited feature/threshold/tree/AutoML search or a prediction score (invalid methods; "
        "R9/R10/R12 fail). Do not conclude that ENTRY-only ML should be retried."
    )
    # R3 stays True for the *class* vs prewritten libraries; remaining-method eligibility
    # still fails because R9/R10/R12 cannot be jointly satisfied without collapsing to M2.
    failed = [k for k, v in gates.items() if v is False]
    eligible = all(gates.values())
    return {
        "METHOD_ID": BOUNDED_DATA_DRIVEN_ID,
        "ALREADY_EXECUTED_EQUIVALENT": False,
        "EQUIVALENT_PRIOR_RUN_ID": None,
        "gates": gates,
        "FAILED_GATES": failed,
        "ELIGIBLE": bool(eligible),
        "WHY": why,
        "INVALID_IF_PURSUED": [
            "ENTRY classifier + fixed EXIT screening",
            "unlimited feature search",
            "unlimited tree depth",
            "unlimited thresholds",
            "AutoML",
            "neural-network architecture search",
            "genetic strategy optimization",
            "Bayesian PnL optimization",
            "another FDG feature",
            "closed-strategy retune",
        ],
    }


def run_eligibility() -> dict[str, Any]:
    cand = bounded_data_driven_eligibility()
    methods = method_taxonomy()
    # No other M1-M10 remains: executed under current unit, or ENTRY/H5/diagnostic only
    # and converting them to Full Causal without new search collapses to M2.
    extra: list[dict[str, Any]] = []
    eligible = [cand] if cand["ELIGIBLE"] else []
    return {
        "candidates_checked": [cand] + extra,
        "ELIGIBLE_RESEARCH_METHOD_N": len(eligible),
        "eligible_method_IDs": [c["METHOD_ID"] for c in eligible],
        "method_rows": methods,
        "bounded": cand,
    }


def decide(elig: dict[str, Any]) -> dict[str, Any]:
    n = int(elig.get("ELIGIBLE_RESEARCH_METHOD_N") or 0)
    if n == 1:
        sid = list(elig.get("eligible_method_IDs") or [None])[0]
        return {
            "CASE": "A",
            "VERDICT": CASE_A,
            "NEXT": NEXT_A if sid == BOUNDED_DATA_DRIVEN_ID else "BOUNDED_FULL_CAUSAL_DATA_DRIVEN_STRATEGY_DESIGN_V1",
            "SELECTED_METHOD_ID": sid,
            "NEW_INFORMATION_OBJECT_SPACE_EXHAUSTED": True,
            "JUSTIFIED_ARCHITECTURE_SPACE_EXHAUSTED": False,
            "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": False,
        }
    return {
        "CASE": "B",
        "VERDICT": CASE_B,
        "NEXT": NEXT_B,
        "SELECTED_METHOD_ID": None,
        "NEW_INFORMATION_OBJECT_SPACE_EXHAUSTED": True,
        "JUSTIFIED_ARCHITECTURE_SPACE_EXHAUSTED": True,
        "JUSTIFIED_RESEARCH_METHOD_SPACE_EXHAUSTED": True,
    }


def build_report_body(*, fdg: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    arches = architecture_inventory()
    methods = method_taxonomy()
    e1 = e1_model_audit()
    joint = joint_full_strategy_audit()
    elig = run_eligibility()
    decision = decide(elig)
    freeze = freeze_method_audit(
        eligible_n=int(elig["ELIGIBLE_RESEARCH_METHOD_N"]),
        selected_id=decision.get("SELECTED_METHOD_ID"),
        why=str(elig["bounded"]["WHY"]),
        r_fail=list(elig["bounded"]["FAILED_GATES"]),
    )
    return {
        "architecture_inventory": arches,
        "method_taxonomy": methods,
        "e1_model_audit": e1,
        "joint_full_strategy_audit": joint,
        "eligibility": elig,
        "freeze": freeze,
        "decision": decision,
        "parent_fdg": fdg,
        "parent_object": obj,
        "closed_architecture_n": len(arches),
        "closed_architecture_ids": [r["ARCHITECTURE_ID"] for r in arches],
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    fdg = dict(report.get("parent_fdg") or {})
    obj = dict(report.get("parent_object") or {})
    methods = {str(m["METHOD_ID"]): m for m in list(report.get("method_taxonomy") or [])}
    e1 = dict(report.get("e1_model_audit") or {})
    joint = dict(report.get("joint_full_strategy_audit") or {})
    elig = dict(report.get("eligibility") or {})
    freeze = dict(report.get("freeze") or {})
    d = dict(report.get("decision") or {})
    return {
        "1_current_FDG_verdict_pinned": bool(fdg.get("ok")),
        "2_FDG_integrity_PASS": fdg.get("integrity") == "33/33",
        "3_FDG_canary_PASS": bool(fdg.get("canary_PASS")),
        "4_FDG_total_PnL": fdg.get("TOTAL_PNL"),
        "5_FDG_PF": fdg.get("PF"),
        "6_FDG_days": fdg.get("pos_neg_zero"),
        "7_FDG_rescued": False,
        "8_FULL_DEPTH_GEOMETRY_exhausted_within_justified_domain": bool(
            fdg.get("FULL_DEPTH_GEOMETRY_JUSTIFIED_MECHANISM_SPACE_EXHAUSTED")
        ),
        "9_new_information_object_search_reopened": False,
        "10_closed_architecture_family_N": report.get("closed_architecture_n"),
        "11_closed_architecture_IDs": report.get("closed_architecture_ids"),
        "12_M1_status": (methods.get("M1") or {}).get("STATUS"),
        "13_M2_status": (methods.get("M2") or {}).get("STATUS"),
        "14_M3_status": (methods.get("M3") or {}).get("STATUS"),
        "15_M4_status": (methods.get("M4") or {}).get("STATUS"),
        "16_M5_status": (methods.get("M5") or {}).get("STATUS"),
        "17_M6_status": (methods.get("M6") or {}).get("STATUS"),
        "18_M7_status": (methods.get("M7") or {}).get("STATUS"),
        "19_M8_status": (methods.get("M8") or {}).get("STATUS"),
        "20_M9_status": (methods.get("M9") or {}).get("STATUS"),
        "21_M10_status": (methods.get("M10") or {}).get("STATUS"),
        "22_E1_X5_decision_unit": e1.get("E1_X5_DECISION_UNIT"),
        "23_E1_X6_decision_unit": e1.get("E1_X6_DECISION_UNIT"),
        "24_ENTRY_EDGE_MASTER_decision_unit": e1.get("ENTRY_EDGE_MASTER_DECISION_UNIT"),
        "25_prior_model_Complete_Full_Causal_at_selection": False,
        "26_prewritten_Full_Causal_library_already_tested": bool(joint.get("PREWRITTEN_FULL_CAUSAL_LIBRARY_ALREADY_TESTED")),
        "27_data_learned_Full_Causal_strategy_already_tested": bool(joint.get("DATA_LEARNED_FULL_CAUSAL_STRATEGY_ALREADY_TESTED")),
        "28_bounded_data_driven_equivalent_prior_run_ID": joint.get("BOUNDED_DATA_DRIVEN_EQUIVALENT_PRIOR_RUN_ID"),
        "29_remaining_eligible_method_N": elig.get("ELIGIBLE_RESEARCH_METHOD_N"),
        "30_eligible_method_IDs": elig.get("eligible_method_IDs"),
        "31_selected_method_ID": d.get("SELECTED_METHOD_ID"),
        "32_why_materially_distinct": freeze.get("WHY_NOT_PRIOR_EQUIVALENT"),
        "33_bounded_before_outcome": False,
        "34_Full_Causal_selection_unit_possible": True,
        "35_explainable_frozen_strategy_possible": False,
        "36_new_strategy_created": False,
        "37_new_ENTRY_created": False,
        "38_new_EXIT_created": False,
        "39_threshold_search": False,
        "40_new_economics_run": False,
        "41_REMAINING_RESEARCH_METHOD_SPEC_SHA256": freeze.get("REMAINING_RESEARCH_METHOD_SPEC_SHA256"),
        "42_Holdout_read": False,
        "43_Stress_read": False,
        "44_future_read": False,
        "45_20260903_plus_read": False,
        "46_20260907_Paper_read": False,
        "47_Sizing": False,
        "48_Runtime_changed": False,
        "49_Capture_changed": False,
        "50_submit_cancel_live": "0/0/0",
        "51_TRUE_OOS": False,
        "52_CERTIFIED": False,
        "53_VERDICT": d.get("VERDICT"),
        "54_NEXT": d.get("NEXT"),
        "object_parent_pinned": bool(obj.get("ok")),
        "RAW_OBJECT_N": obj.get("RAW_OBJECT_N"),
        "UNDEREXPLORED_CAUSAL_OBJECT_N": obj.get("UNDEREXPLORED_CAUSAL_OBJECT_N"),
        "PRIOR_MODEL_METHOD_ENTRY_UNIT_ONLY": True,
    }

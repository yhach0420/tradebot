"""Pin, classify, select. No PnL, markout, or threshold search."""
from __future__ import annotations

from typing import Any

from research.discovery_information_object_expansion_decision_v1 import (
    ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN,
    ANALYSIS_ID,
    CURRENT_42_RETUNE,
    CURRENT_O1_O2_O3_LINE_CLOSED,
    ECONOMICS_RUN,
    ENTRY_SIDE_CAUSED_FAILURE,
    EXIT_SIDE_CAUSED_FAILURE,
    IOAR_EXACT_MECHANISM,
    IOAR_HYPOTHESIS,
    IOAR_VERDICT,
    OUTCOME_READ_N,
    PREOPEN_EXECUTION_VALID,
    THRESHOLD_SEARCH,
    UEIA_VERDICT,
)
from research.discovery_information_object_expansion_decision_v1.objects import (
    apply_schema,
    catalog,
    decide,
    eligible_rows,
    freeze_spec,
    prior_use_rows,
    select_one,
)
from research.discovery_information_object_expansion_decision_v1.spec import pin_ioar, pin_parent, pin_ueia, source_sha256


def run_decision(schema: dict[str, Any]) -> dict[str, Any]:
    parent = pin_parent()
    ioar = pin_ioar()
    ueia = pin_ueia()
    rows = apply_schema(catalog(), schema)
    eligible = eligible_rows(rows)
    selected = select_one(eligible)
    spec = freeze_spec(selected, eligible_n=len(eligible))
    decision = decide(len(eligible), selected, spec)
    under = [r for r in rows if r.get("STATUS") == "UNDEREXPLORED_CAUSAL_OBJECT"]
    return {
        "parent": parent,
        "ioar": ioar,
        "ueia": ueia,
        "raw_objects": rows,
        "prior_use": prior_use_rows(),
        "eligible": eligible,
        "underexplored": under,
        "selected": selected,
        "spec": spec,
        "decision": decision,
        "SOURCE_SHA256": source_sha256(),
        "ANALYSIS_ID": ANALYSIS_ID,
        "CURRENT_O1_O2_O3_LINE_CLOSED": CURRENT_O1_O2_O3_LINE_CLOSED,
        "CURRENT_42_RETUNE": CURRENT_42_RETUNE,
        "ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN": ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN,
        "ENTRY_SIDE_CAUSED_FAILURE": ENTRY_SIDE_CAUSED_FAILURE,
        "EXIT_SIDE_CAUSED_FAILURE": EXIT_SIDE_CAUSED_FAILURE,
        "OUTCOME_READ_N": OUTCOME_READ_N,
        "ECONOMICS_RUN": ECONOMICS_RUN,
        "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
        "PREOPEN_EXECUTION_VALID": PREOPEN_EXECUTION_VALID,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    parent = dict(report.get("parent") or {})
    ioar = dict(report.get("ioar") or {})
    ueia = dict(report.get("ueia") or {})
    schema = dict(report.get("schema") or {})
    decision = dict(report.get("decision") or {})
    spec = dict(report.get("spec") or {})
    selected = report.get("selected")
    under = list(report.get("underexplored") or [])
    eligible = list(report.get("eligible") or [])
    rows = list(report.get("raw_objects") or [])
    sel_id = None if selected is None else selected.get("OBJECT_ID")
    potential = ""
    if selected is not None:
        potential = (
            f"STATE: {selected.get('WHAT_DECISION_STATE_IT_COULD_REPRESENT')} "
            f"BEGIN: {selected.get('HOW_STATE_CAN_BEGIN')} "
            f"INVALIDATE: {selected.get('HOW_STATE_CAN_INVALIDATE')}"
        )
    safety = dict(report.get("safety") or {})
    return {
        "1_parent_CASE_C_pinned": bool(parent.get("ok") and parent.get("CASE") == "C"),
        "2_current_42_closed": bool(CURRENT_O1_O2_O3_LINE_CLOSED),
        "3_current_42_retune": False,
        "4_all_information_already_exhausted": False,
        "5_IOAR_inventoried": bool(ioar.get("ok")),
        "6_exact_IOAR_mechanism": IOAR_EXACT_MECHANISM,
        "7_broader_order_flow_family_automatically_closed": False,
        "8_UEIA_inventoried": bool(ueia.get("ok")),
        "9_raw_object_N": len(rows),
        "10_full_depth_available": bool(schema.get("full_depth_available")),
        "11_event_flow_available": bool(schema.get("event_flow_available")),
        "12_preopen_available": bool(schema.get("preopen_available")),
        "13_prior_session_context_available": bool(schema.get("prior_session_available")),
        "14_underexplored_causal_object_N": len(under),
        "15_underexplored_object_IDs": [r.get("OBJECT_ID") for r in under],
        "16_selected_is_threshold_variation_only": False,
        "17_selected_is_exact_IOAR": False,
        "18_exact_UEIA_reproduction": False,
        "19_exact_closed_Board_mechanism": False,
        "20_eligible_object_N": len(eligible),
        "21_selected_object_ID": sel_id,
        "22_selected_object_DEV_day_N": None if selected is None else selected.get("DEV_DAY_N"),
        "23_timestamp_semantics_proven": bool(schema.get("timestamp_semantics_proven")),
        "24_external_data_required": False,
        "25_future_labels_required": False,
        "26_complete_full_strategy_potential": potential or None,
        "27_PnL_read": False,
        "28_markout_read": False,
        "29_threshold_search": False,
        "30_INFORMATION_OBJECT_SPEC_SHA256": spec.get("INFORMATION_OBJECT_SPEC_SHA256") or decision.get("INFORMATION_OBJECT_SPEC_SHA256"),
        "31_Holdout_read": False,
        "32_Stress_read": False,
        "33_20260903_plus_read": False,
        "34_20260907_Paper_read": False,
        "35_Runtime_changed": False,
        "36_Capture_changed": False,
        "37_submit_cancel_live": "0/0/0",
        "38_VERDICT": decision.get("VERDICT"),
        "39_NEXT": decision.get("NEXT"),
        "IOAR_VERDICT": IOAR_VERDICT,
        "IOAR_HYPOTHESIS": IOAR_HYPOTHESIS,
        "UEIA_VERDICT": UEIA_VERDICT,
        "CURRENT_42_RETUNE": False,
        "OUTCOME_READ_N": 0,
        "safety_submit": safety.get("SUBMIT_N", 0),
    }


def objective_alignment() -> dict[str, Any]:
    return {
        "PRIMARY_GOAL": (
            "Determine whether sealed DEV Capture contains one materially underused "
            "causal INFORMATION OBJECT that justifies one new Complete Full Causal Strategy line."
        ),
        "DIRECTLY_ADVANCES_NEW_LOGIC_COMPLETION": True,
        "NON_GOALS": [
            "feature mining",
            "threshold optimization",
            "old candidate RCA",
            "EXIT retune",
            "CAP retune",
            "O1/O2/O3 rescue",
            "PnL / markout",
            "raw-signal screening",
        ],
        "CURRENT_O1_O2_O3_LINE_CLOSED": True,
        "CURRENT_42_RETUNE": False,
        "ALL_INFORMATION_EXHAUSTION_ALREADY_PROVEN": False,
        "FAILURE_BELONGS_TO": "JOINT_FULL_STRATEGY_IDENTITY",
        "STOP_IF_GOAL_MISMATCH": True,
    }

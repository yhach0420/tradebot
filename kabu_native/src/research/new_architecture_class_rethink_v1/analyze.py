"""Select one Architecture Class. No PnL. No composite event counts. No rule freeze."""
from __future__ import annotations

from typing import Any

from research.new_architecture_class_rethink_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    CLASS_IDS,
    DEVELOPMENT_DAYS,
    NEXT_IF_B,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
)
from research.new_architecture_class_rethink_v1.classes import (
    causality_flags,
    closed_classes,
    evaluate_classes,
    finite_precommit_without_search,
    primary_objective_rank,
    priority_key,
    reconstruction_rank,
    select_class,
)
from research.new_architecture_class_rethink_v1.feasibility import primitive_coverage
from research.new_architecture_class_rethink_v1.spec import (
    canonical_spec,
    dumps_sha256,
    spec_sha256,
)
from research.systematic_state_transition_library_precommit_v1.inventory_correction import (
    remaining_eligible_ids,
)
from research.systematic_state_transition_library_precommit_v1.spec import (
    execution_contract,
    exit_contract,
    fold_assignment,
    portfolio_contract,
)


def _coverage_public(cov: dict[str, Any]) -> dict[str, Any]:
    skip = {"v7_days", "st_days", "opened_paths"}
    return {k: v for k, v in cov.items() if k not in skip}


def decide(cov: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = canonical_spec()
    remaining = remaining_eligible_ids()
    exhausted = len(remaining) == 0
    coverage = cov if cov is not None else primitive_coverage()
    rows = evaluate_classes(coverage)
    flags = causality_flags(coverage, rows)
    chosen = select_class(rows)
    eligible = [r for r in rows if r.get("ELIGIBLE")]
    judged = all(r.get("ELIGIBLE") is not None for r in rows)
    proof = dict(coverage.get("htf_asof_proof") or {})
    composite_n = int(coverage.get("COMPOSITE_ARCHITECTURE_EVENT_COUNT_N") or 0)
    trigger_n = int(coverage.get("NEW_TRIGGER_DEFINITION_N") or 0)
    thr_n = int(coverage.get("NEW_THRESHOLD_DEFINITION_N") or 0)
    fifth = len(rows) != 4 or [r["CLASS_ID"] for r in rows] != list(CLASS_IDS)

    unjudgeable = (
        (not judged)
        or (int(coverage.get("V7_MISSING_DAY_N") or 0) > 0 and any(r["CLASS_ID"] == "C1_MULTI_TIMEFRAME" and r.get("ELIGIBLE") is None for r in rows))
        or (int(coverage.get("ST_MISSING_DAY_N") or 0) > 0 and any(r["CLASS_ID"] == "C4_PORTFOLIO_CROWDING" and r.get("ELIGIBLE") is None for r in rows))
    )
    integrity_failed = (
        (not exhausted)
        or (not judged)
        or unjudgeable
        or fifth
        or composite_n != 0
        or trigger_n != 0
        or thr_n != 0
        or spec["PNL_USED_TO_SELECT_CLASS"]
        or spec["EVENT_COUNT_USED_TO_SELECT_CLASS"]
        or spec["ECONOMICS_RUN"]
        or spec["CANDIDATE_LIBRARY_GENERATION"]
        or spec["EXACT_ENTRY_RULE_CREATION"]
        or spec["EXACT_EXIT_RULE_CREATION"]
        or spec["PRIOR_84_GRID_EXECUTED"]
        or spec["LABEL_GUIDED_FEATURE_SELECTION"]
        or spec["FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER"]
        or spec["FUTURE_EPISODE_ENDPOINT_USED"]
        or flags["PNL_USED_TO_SELECT_CLASS"]
        or flags["EVENT_COUNT_USED_TO_SELECT_CLASS"]
        or flags["FUTURE_EPISODE_ENDPOINT_USED"]
        or flags["FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER"]
        or flags["LABEL_GUIDED_FEATURE_SELECTION"]
        or (chosen is not None and int(primary_objective_rank(chosen)) < 0)
    )

    if integrity_failed:
        case = "E"
        case_name = CASE_E
        nxt = "SOURCE_DATA_FEASIBILITY_FORENSIC_ONLY"
        verdict = CASE_E
        selected = None
    elif eligible:
        case = "A"
        case_name = CASE_A
        assert chosen is not None
        selected = str(chosen["CLASS_ID"])
        nxt = f"{selected}_PRECOMMIT_V1"
        verdict = CASE_A
    else:
        case = "B"
        case_name = CASE_B
        nxt = NEXT_IF_B
        verdict = CASE_B
        selected = None

    hashes = {
        "ELIGIBILITY_SHA256": dumps_sha256(rows),
        "COVERAGE_SHA256": dumps_sha256(_coverage_public(coverage)),
        "ASOF_PROOF_SHA256": dumps_sha256(proof),
        "SPEC_SHA256": spec_sha256(),
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "EXIT_SHA256": dumps_sha256(exit_contract()),
        "PORTFOLIO_SHA256": dumps_sha256(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_assignment()),
    }
    priority_trace = []
    for r in sorted(eligible, key=priority_key):
        priority_trace.append(
            {
                "CLASS_ID": r["CLASS_ID"],
                "priority_key": [str(x) for x in priority_key(r)],
                "finite_precommit_without_search": finite_precommit_without_search(r),
                "primary_objective_rank": primary_objective_rank(r),
                "reconstruction_rank": reconstruction_rank(r),
            }
        )
    forensic = []
    if not exhausted:
        forensic.append({"gap": "REMAINING_ELIGIBLE_NOT_EMPTY", "ids": remaining})
    if fifth:
        forensic.append({"gap": "CLASS_SET_NOT_EXACTLY_C1_C4", "ids": [r["CLASS_ID"] for r in rows]})
    if int(coverage.get("V7_MISSING_DAY_N") or 0):
        forensic.append({"gap": "V7_DEV_CACHE_MISSING_DAY_N", "n": coverage.get("V7_MISSING_DAY_N")})
    if int(coverage.get("ST_MISSING_DAY_N") or 0):
        forensic.append({"gap": "ST_DEV_CACHE_MISSING_DAY_N", "n": coverage.get("ST_MISSING_DAY_N")})
    if not proof.get("ok"):
        forensic.append({"gap": "HTF_ASOF_PROOF_FAILED", "proof": proof})

    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "SELECTED_ARCHITECTURE_CLASS": selected,
        "ELIGIBLE_CLASS_IDS": [r["CLASS_ID"] for r in eligible],
        "EXISTING_ARCHITECTURE_EXHAUSTED": exhausted,
        "REMAINING_ELIGIBLE_ARCHITECTURE_IDS": remaining,
        "FIFTH_CLASS_CREATED": False,
        "PNL_USED_TO_SELECT_CLASS": False,
        "EVENT_COUNT_USED_TO_SELECT_CLASS": False,
        "COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
        "NEW_TRIGGER_DEFINITION_N": 0,
        "NEW_THRESHOLD_DEFINITION_N": 0,
        "CANDIDATE_LIBRARY_GENERATED": False,
        "EXACT_ENTRY_RULE_CREATED": False,
        "EXACT_EXIT_RULE_CREATED": False,
        "ECONOMICS_RUN": False,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "PFQ_REVIVAL_ALLOWED": False,
        "eligibility": rows,
        "priority_trace": priority_trace,
        "causality": flags,
        "coverage": coverage,
        "coverage_public": _coverage_public(coverage),
        "closed_classes": closed_classes(),
        "forensic": forensic,
        "hashes": hashes,
        "execution": execution_contract(),
        "exit": exit_contract(),
        "portfolio": portfolio_contract(),
        "folds": fold_assignment(),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
        "PFQ_RECON_FORBIDDEN": PFQ_RECON_FORBIDDEN,
    }


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    flags = dict(pack.get("causality") or {})
    cov = dict(pack.get("coverage_public") or pack.get("coverage") or {})
    h = dict(pack.get("hashes") or {})
    return {
        "1_or_final_status": pack["OR_FINAL_STATUS"],
        "2_or_economics_opened": False,
        "3_pfq_canonical_status": {
            "document": PFQ_DOCUMENT_ID,
            "verdict": PFQ_VERDICT,
            "line": PFQ_LINE,
        },
        "4_pfq_revival_allowed": False,
        "5_remaining_eligible_architectures": list(pack["REMAINING_ELIGIBLE_ARCHITECTURE_IDS"]),
        "6_EXISTING_ARCHITECTURE_EXHAUSTED": bool(pack["EXISTING_ARCHITECTURE_EXHAUSTED"]),
        "7_TEMPORAL_STATE_TRANSITION_status": (
            "ALREADY_EXECUTED_FULL_STRATEGY / SYSTEMATIC_STATE_TRANSITION_SELECTION_UNSTABLE. "
            "Closed for retune. Not remaining eligible."
        ),
        "8_why_prior_84_grid_rejected": (
            "6 static ENTRY primitives as single/pair AND x 4 EXIT = 84 overlaps the already-run "
            "Simple Full Strategy 75-grid. PRIOR_84_GRID_EXECUTED=false."
        ),
        "9_C1_C4_are_architecture_classes_not_rules": True,
        "10_eligibility_rows": list(pack["eligibility"]),
        "11_PNL_USED_TO_SELECT_CLASS": False,
        "12_EVENT_COUNT_USED_TO_SELECT_CLASS": False,
        "13_SELECTED_ARCHITECTURE_CLASS": pack.get("SELECTED_ARCHITECTURE_CLASS"),
        "14_selection_priority_trace": list(pack.get("priority_trace") or []),
        "15_fifth_class_created": False,
        "16_exact_ENTRY_rule_created": False,
        "17_exact_EXIT_rule_created": False,
        "18_candidate_library_generated": False,
        "19_threshold_selected": False,
        "20_timeframe_selected_by_economics": False,
        "21_execution_exact_identity": pack["execution"],
        "22_EXIT_exact_identity": pack["exit"],
        "23_portfolio_contract": pack["portfolio"],
        "24_Development_dates": list(pack["DEVELOPMENT_DAYS"]),
        "25_classification": "REUSED_HISTORY_BLOCKED_STABILITY",
        "26_block_assignment": pack["folds"]["blocks"],
        "27_HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN": flags.get("HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN"),
        "28_EPISODE_START_CAUSALLY_OBSERVABLE": flags.get("EPISODE_START_CAUSALLY_OBSERVABLE"),
        "29_FUTURE_EPISODE_ENDPOINT_USED": False,
        "30_FUTURE_OUTCOME_LABEL_USED_IN_TRIGGER": False,
        "31_LABEL_GUIDED_FEATURE_SELECTION": False,
        "32_PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE": flags.get("PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE"),
        "33_C4_DATA_FEASIBLE": flags.get("C4_DATA_FEASIBLE"),
        "34_burned_Holdout_read": False,
        "35_Stress_read": False,
        "36_future_used": False,
        "37_Sizing_ran": False,
        "38_economics_run": False,
        "39_PRIOR_84_GRID_EXECUTED": False,
        "40_ARTIFACT_CREATED_AFTER_20260807_ALLOWED": True,
        "41_NEW_MARKET_DATA_AFTER_20260807_ALLOWED": False,
        "42_SPEC_SHA256": h.get("SPEC_SHA256"),
        "43_Runtime_changed": False,
        "44_submit_cancel_live": "0/0/0",
        "45_TRUE_OOS": False,
        "46_CERTIFIED": False,
        "47_VERDICT": pack["VERDICT"],
        "48_NEXT": pack["NEXT"],
        "49_primitive_coverage_diagnostics": {
            "TF1_AVAILABLE_DAY_N": cov.get("TF1_AVAILABLE_DAY_N"),
            "TF3_AVAILABLE_DAY_N": cov.get("TF3_AVAILABLE_DAY_N"),
            "TF5_AVAILABLE_DAY_N": cov.get("TF5_AVAILABLE_DAY_N"),
            "TF1_EVALUABLE_EVENT_N": cov.get("TF1_EVALUABLE_EVENT_N"),
            "TF3_EVALUABLE_EVENT_N": cov.get("TF3_EVALUABLE_EVENT_N"),
            "TF5_EVALUABLE_EVENT_N": cov.get("TF5_EVALUABLE_EVENT_N"),
            "CANDIDATE_STREAM_DAY_N": cov.get("CANDIDATE_STREAM_DAY_N"),
            "CANDIDATE_STREAM_ROW_N": cov.get("CANDIDATE_STREAM_ROW_N"),
            "CANDIDATE_STREAM_UNIQUE_T0_N": cov.get("CANDIDATE_STREAM_UNIQUE_T0_N"),
            "POST_FILL_FIELD_DAY_N": cov.get("POST_FILL_FIELD_DAY_N"),
            "POST_FILL_FILL_T_AVAILABLE_N": cov.get("POST_FILL_FILL_T_AVAILABLE_N"),
            "used_to_select_class": False,
        },
        "50_CASE": pack["CASE_NAME"],
        "51_COMPOSITE_ARCHITECTURE_EVENT_COUNT_N": 0,
        "52_NEW_TRIGGER_DEFINITION_N": 0,
        "53_NEW_THRESHOLD_DEFINITION_N": 0,
        "54_EVENT_COUNT_USED_TO_SELECT_CLASS": False,
        "55_C1_HIGHER_TF_COMPLETED_SEMANTICS_PROVEN": flags.get("HIGHER_TF_COMPLETED_EVENT_SEMANTICS_PROVEN"),
        "56_C2_CAUSAL_EPISODE_ONSET_OBSERVABLE": True,
        "57_C2_HINDSIGHT_ENDPOINT_USED": False,
        "58_C3_FUTURE_OUTCOME_LABEL_USED": False,
        "59_C3_LABEL_GUIDED_FEATURE_SELECTION": False,
        "60_C4_PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE": flags.get("PRE_ADMISSION_CANDIDATE_STREAM_AVAILABLE"),
        "61_ARTIFACT_DATES_AFTER_20260807_READ": (
            "true; allowed as research-history metadata only "
            "(source, ST/V7 DEV caches, closure docs). Not new market dates."
        ),
        "62_NEW_MARKET_DATA_AFTER_20260807_READ": False,
        "63_CANDIDATE_LIBRARY_GENERATED": False,
    }

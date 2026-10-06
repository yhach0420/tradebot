"""Classify remaining architectures. Fixed priority. No PnL. No new economics."""
from __future__ import annotations

from typing import Any

from research.existing_architecture_exhaustion_review_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_C,
    CLOSED_STATUSES,
    NEXT_OR_RECONCILE,
    NEXT_STATE_SPACE,
    REMAINING_STATUSES,
    STATUS_ENUM,
)
from research.existing_architecture_exhaustion_review_v1.inventory import (
    architecture_inventory,
    open_strength_audit,
    x6r3_audit,
)


def remaining_eligible(row: dict[str, Any]) -> bool:
    if str(row.get("CURRENT_STATUS")) not in REMAINING_STATUSES:
        return False
    return bool(
        row.get("PREEXISTING")
        and row.get("EXACT_SOURCE_RECOVERABLE")
        and not row.get("IS_DUPLICATE")
        and not row.get("ALREADY_ROBUST_FAILED")
        and not row.get("INTEGRITY_INVALIDATED")
        and not row.get("ALREADY_FULL_CAUSAL_EVALUATED")
        and row.get("CAUSAL_DATA_REPRODUCIBLE")
        and not row.get("FUTURE_REQUIRED")
    )


def priority_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if row.get("ACTUAL_PAPER_RUN") else 1,
        0 if row.get("EXACT_SOURCE_RECOVERABLE") else 1,
        int(row.get("RECONSTRUCTION_RANK") or 9),
        0 if row.get("HISTORICAL_COVERAGE_PROVEN") else 1,
        str(row.get("FIRST_KNOWN_DATE") or "99999999"),
        str(row.get("ARCHITECTURE_ID") or ""),
    )


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    by = {s: 0 for s in STATUS_ENUM}
    for r in rows:
        by[str(r["CURRENT_STATUS"])] += 1
    closed_n = sum(by[s] for s in CLOSED_STATUSES)
    return {
        "TOTAL_DISTINCT_N": len(rows),
        "CLOSED_N": closed_n,
        "ALREADY_EXECUTED_N": by["ALREADY_EXECUTED_FULL_STRATEGY"],
        "PARTIALLY_TESTED_N": by["PREEXISTING_PARTIALLY_TESTED"],
        "PREEXISTING_UNTESTED_FULL_CAUSAL_N": by["PREEXISTING_UNTESTED_FULL_CAUSAL"],
        "INSUFFICIENT_CLASSIFICATION_N": by["INSUFFICIENT_ARTIFACT_TO_CLASSIFY"],
        "NOT_FULL_STRATEGY_N": by["NOT_FULL_STRATEGY"],
        "NEW_DERIVATIVE_N": by["NEW_DERIVATIVE_NOT_PREEXISTING"],
        **{f"STATUS_{k}": v for k, v in by.items()},
    }


def select_next(eligible: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not eligible:
        return None
    ordered = sorted(eligible, key=priority_key)
    return ordered[0]


def decide(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    inv = list(rows or architecture_inventory())
    ids = [str(r["ARCHITECTURE_ID"]) for r in inv]
    if len(ids) != len(set(ids)):
        raise RuntimeError("DUPLICATE_ARCHITECTURE_ID")
    eligible = [r for r in inv if remaining_eligible(r)]
    insufficient_major = [
        r
        for r in inv
        if r["CURRENT_STATUS"] == "INSUFFICIENT_ARTIFACT_TO_CLASSIFY"
        and r["ARCHITECTURE_ID"]
        in {
            "PRODUCTION_PBV2_MAINLINE",
            "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY",
            "E1_X6R3_CONT_PULL_BREAK",
            "RPFE",
            "VCIE",
            "SIMPLE_TECH_ENTRY_FAMILY",
            "AM_C0",
            "BREAKOUT_CONTINUATION",
            "VWAP_REJECTION_RECLAIM",
            "FAILED_BREAKDOWN_RECLAIM",
            "E4_X2_Z3",
            "RECOVERY_SEQUENCE",
            "PARTICIPATION_ONSET",
        }
    ]
    if insufficient_major:
        case = "C"
        case_name = CASE_C
        nxt = "SOURCE_ARTIFACT_FORENSIC_ONLY"
        verdict = CASE_C
        chosen = None
    elif eligible:
        case = "A"
        case_name = CASE_A
        chosen = select_next(eligible)
        assert chosen is not None
        nxt = (
            NEXT_OR_RECONCILE
            if chosen["ARCHITECTURE_ID"] == "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"
            else f"RECONCILE_{chosen['ARCHITECTURE_ID']}_V1"
        )
        verdict = CASE_A
    else:
        case = "B"
        case_name = CASE_B
        nxt = NEXT_STATE_SPACE
        verdict = CASE_B
        chosen = None
    osa = open_strength_audit()
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "SELECTED_NEXT_ARCHITECTURE": None if chosen is None else chosen["ARCHITECTURE_ID"],
        "PNL_USED_TO_SELECT_NEXT": False,
        "NEW_ARCHITECTURE_CREATED": False,
        "NEW_ECONOMICS_RUN": False,
        "STRESS_RAW_READ": False,
        "STRESS_NEW_METRICS": False,
        "FUTURE_USED": False,
        "MAX_NEW_DATA_DATE": "NONE",
        "RUNTIME_CHANGED": False,
        "SUBMIT_CANCEL_LIVE": "0/0/0",
        "OR_SLEEVE_ISOLATED_FULL_CAUSAL_ALREADY_RUN": bool(osa["OR_SLEEVE_ISOLATED_FULL_CAUSAL_ALREADY_RUN"]),
        "counts": counts(inv),
        "remaining_eligible_ids": [r["ARCHITECTURE_ID"] for r in sorted(eligible, key=priority_key)],
        "priority_order": [
            {
                "ARCHITECTURE_ID": r["ARCHITECTURE_ID"],
                "priority_key": [str(x) for x in priority_key(r)],
                "ACTUAL_PAPER_RUN": r["ACTUAL_PAPER_RUN"],
                "RECONSTRUCTION_RANK": r["RECONSTRUCTION_RANK"],
                "FIRST_KNOWN_DATE": r["FIRST_KNOWN_DATE"],
            }
            for r in sorted(eligible, key=priority_key)
        ],
        "open_strength_audit": osa,
        "x6r3_audit": x6r3_audit(),
        "inventory": inv,
    }


def family_status(inv: list[dict[str, Any]], architecture_id: str) -> str:
    for r in inv:
        if r["ARCHITECTURE_ID"] == architecture_id:
            return str(r["CURRENT_STATUS"])
    return "MISSING"


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    inv = list(pack["inventory"])
    osa = dict(pack["open_strength_audit"])
    x6 = dict(pack["x6r3_audit"])
    c = dict(pack["counts"])
    return {
        "1_why_direct_open_strength_standalone_deferred": (
            "Production OR is not a standalone strategy. Exact path is Stage2 PBv2 reject → "
            "OR overlay evaluation → O_R003 day-high → update_count → OS9/day_leader → OR cap=1 → accept. "
            "Removing the PBv2-reject requirement, changing CAP 1→5, or adding new EXIT ZV/Z3/ZH is a "
            "NEW DERIVATIVE, not preexisting OR."
        ),
        "2_exact_production_or_role": osa["PRODUCTION_OR_ROLE"],
        "3_production_or_exact_cap_semantics": osa["C_OR_CAP"],
        "4_production_or_exact_entry_architecture": osa["A_OR_ENTRY_PREDICATE"],
        "5_production_or_classic_opening_range_breakout": False,
        "6_standalone_open_strength_preexisting": False,
        "7_phase687w27_prior_or_era_portfolio_evidence": osa["E_OR_OBSERVED_EVIDENCE"],
        "8_or_sleeve_isolated_full_causal_already_run": False,
        "9_or_sleeve_current_status": family_status(inv, "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"),
        "10_x6_x6r3_final_status": family_status(inv, "E1_X6R3_CONT_PULL_BREAK"),
        "10b_x6_score_joint": x6["SCORE_JOINT_VERDICT"],
        "10c_x6r3_phase_b": x6["X6R3_PHASE_B_VERDICT"],
        "10d_fcrr": x6["FCRR_VERDICT"],
        "10e_taer": x6["TAER_VERDICT"],
        "11_rpfe_final_status": family_status(inv, "RPFE"),
        "12_vcie_final_status": family_status(inv, "VCIE"),
        "13_simple_tech_final_status": family_status(inv, "SIMPLE_TECH_ENTRY_FAMILY"),
        "14_c0_am_final_status": family_status(inv, "AM_C0"),
        "15_breakout_final_status": family_status(inv, "BREAKOUT_CONTINUATION"),
        "16_vwap_fdr_final_status": {
            "VWAP_REJECTION_RECLAIM": family_status(inv, "VWAP_REJECTION_RECLAIM"),
            "FAILED_BREAKDOWN_RECLAIM": family_status(inv, "FAILED_BREAKDOWN_RECLAIM"),
        },
        "17_e4_final_status": family_status(inv, "E4_X2_Z3"),
        "18_recovery_final_status": family_status(inv, "RECOVERY_SEQUENCE"),
        "19_participation_final_status": family_status(inv, "PARTICIPATION_ONSET"),
        "20_total_distinct_architecture_n": c["TOTAL_DISTINCT_N"],
        "21_closed_n": c["CLOSED_N"],
        "22_already_executed_n": c["ALREADY_EXECUTED_N"],
        "23_partially_tested_n": c["PARTIALLY_TESTED_N"],
        "24_preexisting_untested_full_causal_n": c["PREEXISTING_UNTESTED_FULL_CAUSAL_N"],
        "25_insufficient_classification_n": c["INSUFFICIENT_CLASSIFICATION_N"],
        "26_complete_architecture_inventory_table": [
            {
                "ARCHITECTURE_ID": r["ARCHITECTURE_ID"],
                "FIRST_KNOWN_DATE": r["FIRST_KNOWN_DATE"],
                "CURRENT_STATUS": r["CURRENT_STATUS"],
                "FINAL_VERDICT": r["FINAL_VERDICT"],
                "PREEXISTING": r["PREEXISTING"],
                "ACTUAL_PAPER_RUN": r["ACTUAL_PAPER_RUN"],
                "FULL_CAUSAL_RUN": r["FULL_CAUSAL_RUN"],
                "REMAINING_ELIGIBLE": remaining_eligible(r),
            }
            for r in inv
        ],
        "27_remaining_eligible_architecture_ids": list(pack["remaining_eligible_ids"]),
        "28_fixed_priority_selected_next": pack["NEXT"],
        "29_pnl_used_to_select_next": False,
        "30_new_architecture_created": False,
        "31_new_economics_run": False,
        "32_stress_raw_read": False,
        "33_stress_new_metrics": False,
        "34_future_used": False,
        "35_max_new_data_date": "NONE",
        "36_runtime_changed": False,
        "37_submit_cancel_live": "0/0/0",
        "38_case": pack["CASE_NAME"],
        "39_verdict": pack["VERDICT"],
        "40_next": pack["NEXT"],
    }

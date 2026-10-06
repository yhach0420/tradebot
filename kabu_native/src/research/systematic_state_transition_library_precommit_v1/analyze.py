"""Assemble frozen library. No economics. No harvest. No Stress/future."""
from __future__ import annotations

import json
from typing import Any

from research.systematic_state_transition_library_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    DEVELOPMENT_DAYS,
    NEXT_IF_A,
    NEXT_IF_B,
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_VERDICT,
)
from research.systematic_state_transition_library_precommit_v1.duplicate_audit import apply_prune, audit_raw_library
from research.systematic_state_transition_library_precommit_v1.inventory_correction import (
    corrected_inventory,
    or_closure_from_report,
    pfq_closure,
    remaining_eligible_ids,
)
from research.systematic_state_transition_library_precommit_v1.isolation import NATIVE, RESEARCH_ROOT
from research.systematic_state_transition_library_precommit_v1.library import build_raw_library
from research.systematic_state_transition_library_precommit_v1.spec import (
    canonical_spec,
    coverage_gates,
    dumps_sha256,
    economic_gates,
    execution_contract,
    exit_contract,
    fold_assignment,
    portfolio_contract,
    spec_sha256,
    stability_gates,
)
from research.systematic_state_transition_library_precommit_v1.state_registry import build_state_registry


def _load_or_report() -> dict[str, Any]:
    path = RESEARCH_ROOT / "or_overlay_causal_contribution_reconciliation_v1" / "report.json"
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _source_files_ok(registry: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    missing = []
    for r in registry:
        for part in str(r["SOURCE_PATH"]).split(";"):
            rel = part.strip()
            if not rel:
                continue
            p = NATIVE.joinpath(*rel.replace("\\", "/").split("/"))
            if not p.is_file():
                missing.append(rel)
    return (not missing), missing


def decide() -> dict[str, Any]:
    spec = canonical_spec()
    inv = corrected_inventory()
    remaining = remaining_eligible_ids(inv)
    exhausted = len(remaining) == 0
    registry = build_state_registry()
    src_ok, missing_src = _source_files_ok(registry)
    unavailable = [r for r in registry if not r.get("AVAILABLE")]
    available = [r for r in registry if r.get("AVAILABLE")]
    pnl_used = any(r.get("PNL_USED") for r in registry)
    tuned = any(r.get("THRESHOLD_TUNED_THIS_RUN") for r in registry)
    lookahead = any(r.get("LOOKAHEAD") for r in registry)
    integrity = any(r.get("INTEGRITY_ISSUE") for r in registry)
    raw = build_raw_library(registry)
    dup_map = audit_raw_library(raw)
    final, counts = apply_prune(raw, dup_map)
    unknown_n = int(counts["UNKNOWN_IDENTITY_N"])
    templates_ok = all(c["TEMPLATE"] in ("PERSIST_NEXT", "HANDOFF_NEXT") for c in raw)
    or_rep = or_closure_from_report(_load_or_report())
    pfq = pfq_closure()

    integrity_failed = (
        (not src_ok)
        or (not exhausted)
        or pnl_used
        or tuned
        or lookahead
        or integrity
        or unknown_n > 0
        or (not templates_ok)
        or int(counts["RAW_CANDIDATE_N"]) > 25
        or spec["PRIOR_84_GRID_EXECUTED"]
        or spec["STATIC_AND_GRID"]
        or spec["ECONOMICS_RUN"]
        or spec["CANDIDATE_BACKFILL"]
    )

    if integrity_failed:
        case = "E"
        case_name = CASE_E
        nxt = "DO_NOT_RUN_ECONOMICS"
        verdict = CASE_E
    elif int(counts["FINAL_CANDIDATE_N"]) == 0:
        case = "B"
        case_name = CASE_B
        nxt = NEXT_IF_B
        verdict = CASE_B
    elif int(counts["FINAL_CANDIDATE_N"]) >= 1 and exhausted and src_ok:
        case = "A"
        case_name = CASE_A
        nxt = NEXT_IF_A
        verdict = CASE_A
    else:
        case = "E"
        case_name = CASE_E
        nxt = "DO_NOT_RUN_ECONOMICS"
        verdict = CASE_E

    hashes = {
        "STATE_REGISTRY_SHA256": dumps_sha256(registry),
        "RAW_LIBRARY_SHA256": dumps_sha256(raw),
        "DUPLICATE_MAP_SHA256": dumps_sha256(dup_map),
        "FINAL_CANDIDATE_LIBRARY_SHA256": dumps_sha256(final),
        "EXECUTION_SHA256": dumps_sha256(execution_contract()),
        "EXIT_SHA256": dumps_sha256(exit_contract()),
        "PORTFOLIO_CONTRACT_SHA256": dumps_sha256(portfolio_contract()),
        "FOLD_ASSIGNMENT_SHA256": dumps_sha256(fold_assignment()),
        "GATES_SHA256": dumps_sha256(
            {
                "coverage": coverage_gates(),
                "economic": economic_gates(),
                "stability": stability_gates(),
            }
        ),
        "SPEC_SHA256": spec_sha256(),
    }
    return {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CASE": case,
        "CASE_NAME": case_name,
        "VERDICT": verdict,
        "NEXT": nxt,
        "EXISTING_ARCHITECTURE_EXHAUSTED": exhausted,
        "REMAINING_ELIGIBLE_ARCHITECTURE_IDS": remaining,
        "AVAILABLE_STATE_N": len(available),
        "STATE_FAMILY_UNAVAILABLE_N": len(unavailable),
        "MISSING_SOURCE_PATHS": missing_src,
        "PRIOR_84_GRID_EXECUTED": False,
        "PRIOR_84_GRID_REJECTED_AS_REDUNDANT": True,
        "THRESHOLD_TUNED_THIS_RUN": False,
        "STATIC_AND_GRID": False,
        "CANDIDATE_BACKFILL": False,
        "ECONOMICS_RUN": False,
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "PFQ_REVIVAL_ALLOWED": False,
        "inventory_correction": inv,
        "pfq_closure": pfq,
        "or_closure": or_rep,
        "state_registry": registry,
        "raw_library": raw,
        "duplicate_map": dup_map,
        "final_library": final,
        "counts": counts,
        "hashes": hashes,
        "execution": execution_contract(),
        "exit": exit_contract(),
        "portfolio": portfolio_contract(),
        "folds": fold_assignment(),
        "coverage_gates": coverage_gates(),
        "economic_gates": economic_gates(),
        "stability_gates": stability_gates(),
        "DEVELOPMENT_DAYS": list(DEVELOPMENT_DAYS),
    }


def closed_lineage_rows(dup_map: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [r for r in dup_map if r.get("ACTION") == "EXCLUDE_CLOSED_LINEAGE" or r.get("CLOSED_LINEAGE_REPRODUCTION")]
    if rows:
        return rows
    return [
        {
            "CANDIDATE_ID": "NONE",
            "ACTION": "NONE",
            "WHY": (
                "No candidate excluded as closed-lineage reproduction. "
                "Shared Simple-Tech / VWAP / volume primitives do not by themselves reproduce "
                "first-cross AND grids, T1 10s onset, 3-state recovery, or quote-inferred VCIE."
            ),
        }
    ]


def build_answers(pack: dict[str, Any]) -> dict[str, Any]:
    h = dict(pack["hashes"])
    c = dict(pack["counts"])
    dup_excl = [r for r in pack["duplicate_map"] if r.get("ACTION") == "EXCLUDE_DUPLICATE"]
    closed_excl = [r for r in pack["duplicate_map"] if r.get("ACTION") == "EXCLUDE_CLOSED_LINEAGE"]
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
        "7_why_prior_84_grid_rejected": (
            "6 static ENTRY primitives as single/pair AND x 4 EXIT = 84 overlaps the already-run "
            "Simple Full Strategy 75-grid. Examples: Close>prevClose AND Close>VWAP ~ E1_UPTREND; "
            "Close>prevClose AND Volume>median10 ~ E2_VOLUME_CONTINUATION; Z1 VWAP loss and "
            "Z3 two-bar weakness already executed. PRIOR_84_GRID_EXECUTED=false; "
            "PRIOR_84_GRID_REJECTED_AS_REDUNDANT=true."
        ),
        "8_available_state_family_n": int(pack["AVAILABLE_STATE_N"]),
        "9_exact_state_registry": [
            {
                "STATE_ID": r["STATE_ID"],
                "FAMILY": r["FAMILY"],
                "SOURCE_PATH": r["SOURCE_PATH"],
                "SOURCE_FUNCTION": r["SOURCE_FUNCTION"],
                "EXACT_DEFINITION": r["EXACT_DEFINITION"],
                "TIMEFRAME": r["TIMEFRAME"],
                "EVENT_TIMESTAMP_SEMANTICS": r["EVENT_TIMESTAMP_SEMANTICS"],
                "LOOKBACK": r["LOOKBACK"],
                "THRESHOLD_SOURCE": r["THRESHOLD_SOURCE"],
                "THRESHOLD_TUNED_THIS_RUN": r["THRESHOLD_TUNED_THIS_RUN"],
                "SOURCE_SHA256": r["SOURCE_SHA256"],
            }
            for r in pack["state_registry"]
        ],
        "10_threshold_tuned": False,
        "11_ENTRY_templates": ["PERSIST_NEXT", "HANDOFF_NEXT"],
        "12_static_AND_grid_used": False,
        "13_raw_candidate_n": int(c["RAW_CANDIDATE_N"]),
        "14_duplicate_candidate_n": int(c["DUPLICATE_CANDIDATE_N"]),
        "15_closed_lineage_candidate_n": int(c["CLOSED_LINEAGE_CANDIDATE_N"]),
        "16_final_candidate_n": int(c["FINAL_CANDIDATE_N"]),
        "17_duplicate_ids_and_prior_matching_architecture": [
            {"CANDIDATE_ID": r["CANDIDATE_ID"], "MATCHING_ARCHITECTURE_ID": r.get("MATCHING_ARCHITECTURE_ID") or r.get("NEAREST_ARCHITECTURE")}
            for r in dup_excl
        ]
        or "NONE",
        "18_candidate_backfill_performed": False,
        "19_execution_exact_identity": pack["execution"],
        "20_EXIT_exact_identity": pack["exit"],
        "21_portfolio_contract": pack["portfolio"],
        "22_Development_dates": list(pack["DEVELOPMENT_DAYS"]),
        "23_blocked_OOS": False,
        "24_internal_validation": False,
        "25_classification": "REUSED_HISTORY_BLOCKED_STABILITY",
        "26_block_assignment": pack["folds"]["blocks"],
        "27_coverage_gates": pack["coverage_gates"],
        "28_economic_gates": pack["economic_gates"],
        "29_causal_ex_top_rule": pack["economic_gates"]["CAUSAL_EX_TOP1"],
        "30_robust_score": pack["economic_gates"]["ROBUST_SCORE"],
        "31_stability_gates": pack["stability_gates"],
        "32_burned_Holdout_read": False,
        "33_Stress_read": False,
        "34_future_used": False,
        "35_Sizing_ran": False,
        "36_economics_run": False,
        "37_STATE_REGISTRY_SHA256": h["STATE_REGISTRY_SHA256"],
        "38_RAW_LIBRARY_SHA256": h["RAW_LIBRARY_SHA256"],
        "39_DUPLICATE_MAP_SHA256": h["DUPLICATE_MAP_SHA256"],
        "40_FINAL_CANDIDATE_LIBRARY_SHA256": h["FINAL_CANDIDATE_LIBRARY_SHA256"],
        "41_FOLD_ASSIGNMENT_SHA256": h["FOLD_ASSIGNMENT_SHA256"],
        "42_SPEC_SHA256": h["SPEC_SHA256"],
        "43_Runtime_changed": False,
        "44_submit_cancel_live": "0/0/0",
        "45_TRUE_OOS": False,
        "46_CERTIFIED": False,
        "47_VERDICT": pack["VERDICT"],
        "48_NEXT": pack["NEXT"],
        "_closed_lineage_excluded_n": len(closed_excl),
    }

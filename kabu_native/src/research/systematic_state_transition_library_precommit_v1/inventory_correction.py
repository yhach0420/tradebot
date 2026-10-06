"""Latest-SoT remaining-eligible recalculation. Prior inventory artifacts not mutated."""
from __future__ import annotations

from typing import Any

from research.existing_architecture_exhaustion_review_v1.analyze import remaining_eligible
from research.existing_architecture_exhaustion_review_v1.inventory import architecture_inventory
from research.systematic_state_transition_library_precommit_v1 import (
    OR_FINAL_STATUS,
    PFQ_DOCUMENT_ID,
    PFQ_LINE,
    PFQ_RECON_FORBIDDEN,
    PFQ_VERDICT,
)

OR_ID = "PRODUCTION_OR_OPEN_STRENGTH_OVERLAY"
PFQ_ID = "E1_X7_PFQ"

OR_UNRESOLVABLE_REASONS = {
    "COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE": False,
    "PBV2_CAP5_COUNTERFACTUAL_PROVABLE": False,
    "OR_REJECT_REPLAYABLE_N": 0,
    "DATE_SCOPED_RUNTIME_IDENTITY_PROVEN": False,
    "FULL_CAUSAL_PARITY": False,
    "CALLED_ECONOMIC_FAIL": False,
}


def corrected_inventory() -> list[dict[str, Any]]:
    rows = []
    for r in architecture_inventory():
        aid = str(r["ARCHITECTURE_ID"])
        orig_remaining = bool(remaining_eligible(r))
        status = str(r["CURRENT_STATUS"])
        remaining = orig_remaining
        why = str(r.get("WHY_CLOSED_OR_OPEN") or "")
        if aid == OR_ID:
            status = OR_FINAL_STATUS
            remaining = False
            why = (
                "OR overlay causal contribution is unresolvable: "
                "COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE=false; "
                "PBV2_CAP5_COUNTERFACTUAL_PROVABLE=false; OR_REJECT_REPLAYABLE_N=0; "
                "DATE_SCOPED_RUNTIME_IDENTITY_PROVEN=false; FULL_CAUSAL_PARITY=false. "
                "Not an economic FAIL. remaining_eligible=false."
            )
        elif aid == PFQ_ID:
            status = PFQ_LINE
            remaining = False
            why = (
                f"Canonical document {PFQ_DOCUMENT_ID}; verdict {PFQ_VERDICT}; "
                f"line {PFQ_LINE}; robust_entry_exit_pair=null; frozen_candidate=null; "
                f"restart prohibited; {PFQ_RECON_FORBIDDEN}=DO_NOT_RUN."
            )
        rows.append(
            {
                "ARCHITECTURE_ID": aid,
                "ORIGINAL_STATUS": r["CURRENT_STATUS"],
                "CORRECTED_STATUS": status,
                "ORIGINAL_REMAINING_ELIGIBLE": orig_remaining,
                "CORRECTED_REMAINING_ELIGIBLE": remaining,
                "FINAL_VERDICT": r["FINAL_VERDICT"] if aid not in {OR_ID, PFQ_ID} else (
                    "OR_OVERLAY_CAUSAL_RECONCILIATION_UNRESOLVABLE" if aid == OR_ID else PFQ_VERDICT
                ),
                "WHY": why,
                "PREEXISTING": r["PREEXISTING"],
                "FIRST_KNOWN_DATE": r["FIRST_KNOWN_DATE"],
            }
        )
    return rows


def remaining_eligible_ids(rows: list[dict[str, Any]] | None = None) -> list[str]:
    inv = rows if rows is not None else corrected_inventory()
    return [r["ARCHITECTURE_ID"] for r in inv if r["CORRECTED_REMAINING_ELIGIBLE"]]


def pfq_closure() -> dict[str, Any]:
    return {
        "canonical_document": PFQ_DOCUMENT_ID,
        "verdict": PFQ_VERDICT,
        "PFQ": PFQ_LINE,
        "robust_entry_exit_pair": None,
        "frozen_candidate": None,
        "restart_prohibited": True,
        "revival_allowed": False,
        "E1_X7_PFQ_FULL_CAUSAL_RECONCILIATION_V1": "DO_NOT_RUN",
        "SOURCE_PATH": "src/research/e1_x7_x9_closure/__init__.py",
    }


def or_closure_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    d = dict((report or {}).get("decision") or {})
    summary = dict(d.get("summary") or {})
    return {
        "OR_FINAL_STATUS": OR_FINAL_STATUS,
        "OR_ECONOMICS_OPENED": False,
        "CALLED_ECONOMIC_FAIL": False,
        "remaining_eligible": False,
        "VERDICT": d.get("VERDICT") or "OR_OVERLAY_CAUSAL_RECONCILIATION_UNRESOLVABLE",
        "CASE_NAME": d.get("CASE_NAME") or "OR_OVERLAY_CAUSAL_RECONCILIATION_UNRESOLVABLE",
        "COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE": bool(
            summary.get("COUNTERFACTUAL_CANDIDATE_STREAM_COMPLETE", False)
        ),
        "PBV2_CAP5_COUNTERFACTUAL_PROVABLE": bool(summary.get("PBV2_CAP5_COUNTERFACTUAL_PROVABLE", False)),
        "OR_REJECT_REPLAYABLE_N": int(summary.get("OR_REJECT_REPLAYABLE_N") or 0),
        "DATE_SCOPED_RUNTIME_IDENTITY_PROVEN": bool(summary.get("DATE_SCOPED_RUNTIME_IDENTITY_PROVEN", False)),
        "FULL_CAUSAL_PARITY": bool(summary.get("FULL_CAUSAL_PARITY", False)),
        "CONTROL_A_VALID": bool(d.get("CONTROL_A_VALID", False)),
        "CONTROL_B_VALID": bool(d.get("CONTROL_B_VALID", False)),
        "PFQ_RECON_SUPERSEDED": True,
        "NEXT_FROM_OR_RUN_SUPERSEDED_BY": "SYSTEMATIC_STATE_TRANSITION_LIBRARY_PRECOMMIT_V1",
        **OR_UNRESOLVABLE_REASONS,
    }

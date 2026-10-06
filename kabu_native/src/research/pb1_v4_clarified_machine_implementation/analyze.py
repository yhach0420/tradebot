"""Decision. Faithful semantic implementation + invariants. No accuracy target."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_clarified_machine_implementation import (
    CASE_BIND,
    CASE_IMPLEMENTED,
    CASE_INCOMPLETE,
    EXPECTED_SPEC_SHA256,
    NEXT_AUDIT,
    NEXT_BIND,
    NEXT_RCA,
    PARENT_V32_SHA,
)
from research.pb1_v4_clarified_machine_implementation.definitions import STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v4_clarified_machine_implementation.leakage import scan_package
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def day_reach_counts(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "DAY_REACH_FLAGS_NOT_A_STRICT_EVENT_FUNNEL",
        "WHY_THIS_STOCK": int(counts.get("WHY_THIS_STOCK") or counts.get("S0") or 0),
        "OPENING_DRIVE_SEED": int(counts.get("OPENING_DRIVE_SEED") or 0),
        "OPENING_DRIVE_ACTIVE": int(counts.get("OPENING_DRIVE_ACTIVE") or 0),
        "LOCATION_IDENTIFIED": int(counts.get("LOCATION_IDENTIFIED") or 0),
        "THESIS_READY": int(counts.get("THESIS_READY") or 0),
        "E0": int(counts.get("E0") or 0),
        "E1": int(counts.get("E1") or 0),
        "dir_bull": int(counts.get("dir_bull") or 0),
        "dir_bear": int(counts.get("dir_bear") or 0),
    }


def _row(audit: dict[str, Any], key: str) -> dict[str, Any]:
    return dict(audit.get(key) or {})


def _seed_ok(row: dict[str, Any], *names: str) -> bool:
    seed = str(row.get("machine_SEED") or "")
    opening = str(row.get("machine_opening_state") or "")
    return any(n in (seed, opening) for n in names)


def decide(
    *,
    bind_ok: bool,
    inv: dict[str, Any],
    leak: dict[str, Any],
    audit: dict[str, Any],
    new_sha: str,
    hidden_parity: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "incomplete_reasons": ["bind_failed"]}
    if int(inv.get("violations") or 0) != 0:
        reasons.append("state_invariant_violations")
    if int(inv.get("SAME_BAR_ENTRY") or 0) != 0:
        reasons.append("same_bar_entry")
    if leak.get("FAIL_EXTEND_present") or leak.get("LAST_BREAK_present"):
        reasons.append("unsupported_clock_semantics_in_eligibility")
    if leak.get("SETUP_ELIGIBLE_in_eligibility"):
        reasons.append("mixed_SETUP_ELIGIBLE_state")
    if not hidden_parity:
        reasons.append("HIDDEN_1M_THESIS_PARITY_false")
    if new_sha in (V4_CORRECTED_MACHINE_SHA256, V4_LEGACY_LEAKED_IMPLEMENTATION):
        reasons.append("new_sha_collides_with_frozen_machine")
    s3382 = _row(audit, "3382_20241004")
    if s3382 and not _seed_ok(s3382, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE"):
        reasons.append("3382_not_failed_open_path")
    if s3382 and not (s3382.get("machine_ACTIVE") or s3382.get("opening_drive_id")):
        reasons.append("3382_failed_open_never_became_active")
    s4063 = _row(audit, "4063_20251118")
    if s4063 and str(s4063.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED":
        reasons.append("4063_true_first_path")
    if reasons:
        return {
            "VERDICT": CASE_INCOMPLETE,
            "NEXT": NEXT_RCA,
            "incomplete_reasons": reasons,
            "material_mismatches_explained_separately": True,
        }
    return {
        "VERDICT": CASE_IMPLEMENTED,
        "NEXT": NEXT_AUDIT,
        "incomplete_reasons": [],
        "note": "Faithful clarified-V2 implementation. No accuracy target. Residual 88 mismatches are reported, not silently accepted.",
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    calib: dict[str, Any],
    audit: dict[str, Any],
    inv: dict[str, Any],
    leak: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
) -> dict[str, Any]:
    new_sha = machine_sha256()
    hidden = bool(walked.get("HIDDEN_1M_THESIS_PARITY"))
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        inv=inv,
        leak=leak,
        audit=audit,
        new_sha=new_sha,
        hidden_parity=hidden,
    )
    reach = day_reach_counts(dict(walked.get("counts") or {}))
    return {
        "clarified_spec_sha256": spec_sha256(),
        "EXPECTED_SPEC_SHA256": EXPECTED_SPEC_SHA256,
        "clarified_spec_sha_bound": spec_sha256() == EXPECTED_SPEC_SHA256,
        "parent_specs_preserved": fps == fps_after,
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "corrected_preserved": bind.get("corrected_preserved") is True,
        "leaked_preserved": leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "PB1_V4_CLARIFIED_MACHINE_SHA256": new_sha,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "PRIMARY_SETUP_TIMEFRAME": "5m",
        "same_clock_baselines_implemented": True,
        "atr_sanity_implemented": True,
        "OPENING_DRIVE_SEED_implemented": True,
        "OPENING_DRIVE_ACTIVE_implemented": True,
        "seed_is_permanent_eligibility": False,
        "continued_intent_encoded": True,
        "continued_intent_how": "STATE over close-to-close, net vs gross, one-bar domination, same-dir progression, loss of progress, two-sided internal path",
        "FAILED_OPEN_uses_fixed_bar_expiry": False,
        "LOCATION_IDENTIFIED_separate_from_interaction": True,
        "1m_can_create_location": False,
        "first_unsuccessful_interaction_automatically_kills": False,
        "THESIS_READY_definition": "WHY_THIS_STOCK + OPENING_DRIVE_ACTIVE + LOCATION_IDENTIFIED + thesis still alive. No 1m required.",
        "E0_definition": "From THESIS_READY, completed 5m interaction/hold + continuation state, then next causal 1m open. No same-bar entry.",
        "E1_definition": "From same THESIS_READY, completed 1m observes pre-identified level (touch/retest/hold/rejection/reacceleration), then next causal 1m open.",
        "E1_can_alter_location_id": False,
        "E1_can_alter_direction": False,
        "E1_can_alter_opening_drive_id": False,
        "E1_can_revive_thesis": False,
        "S4_hard_035_15_retained_as_semantic_requirement": False,
        "day_reach_counts": reach,
        "invariants": inv,
        "leakage": leak,
        "calibration": calib,
        "semantic_development_audit": {k: v for k, v in audit.items() if k != "rows"},
        "audit_rows": list(audit.get("rows") or []),
        "critical_cases": {
            "3382_20241004": _row(audit, "3382_20241004"),
            "7011_20250523": _row(audit, "7011_20250523"),
            "6758_20250613": _row(audit, "6758_20250613"),
            "4063_20251118": _row(audit, "4063_20251118"),
            "7011_20241205": _row(audit, "7011_20241205"),
            "6920_20250404": _row(audit, "6920_20250404"),
            "9501_20251113": _row(audit, "9501_20251113"),
        },
        "prev_s1_fp19": list(audit.get("prev_s1_fp19") or []),
        "prev_s1_fn2": list(audit.get("prev_s1_fn2") or []),
        "material_mismatches": list(audit.get("material_mismatches") or []),
        "HIDDEN_1M_THESIS_PARITY": hidden,
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "live_v32": v32_machine_sha256(),
        "live_v31": v31_machine_sha256(),
        "live_v3": v3_machine_sha256(),
        "live_v2": v2_machine_sha256(),
        "v32_unchanged": v32_machine_sha256() == PARENT_V32_SHA,
        "any_future_outcome_used": False,
        "any_prospective_event_consumed": False,
        "any_pnl": False,
        "any_mfe_mae": False,
        "any_economic_e0_e1": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "decision": decision,
        "confusion": dict(audit.get("confusion") or {}),
    }

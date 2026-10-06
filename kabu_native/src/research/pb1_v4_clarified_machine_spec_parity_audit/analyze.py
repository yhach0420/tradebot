"""Parity decision. Accuracy is not the gate."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_implementation.invariants import count_invariants
from research.pb1_v4_clarified_machine_implementation.leakage import scan_package
from research.pb1_v4_clarified_machine_spec_parity_audit import CASE_BIND, CASE_FAIL, CASE_PASS, NEXT_BIND, NEXT_PREP, NEXT_RCA


def _slim(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "research_pool_n": bind.get("research_pool_n"),
        "live_spec_sha256": bind.get("live_spec_sha256"),
        "live_machine_sha256": bind.get("live_machine_sha256"),
        "report_machine_sha256": bind.get("report_machine_sha256"),
        "machine_sha_unchanged": bind.get("machine_sha_unchanged"),
        "spec_sha_unchanged": bind.get("spec_sha_unchanged"),
        "corrected_preserved": bind.get("corrected_preserved"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_ready_verdict": bind.get("parent_ready_verdict"),
        "parent_clarified_verdict": bind.get("parent_clarified_verdict"),
        "machine_verdict": bind.get("machine_verdict"),
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
            "discovery_first": (list(split.get("discovery_dates") or []) or [None])[0],
            "discovery_last": (list(split.get("discovery_dates") or []) or [None])[-1],
        },
        "blocks": {"ok": blocks.get("ok"), "block_sha256": blocks.get("block_sha256")},
        "symbol_n": len(list(bind.get("symbols") or [])),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def decide(
    *,
    bind_ok: bool,
    hashes_unchanged: bool,
    hidden_mismatch_n: int,
    one_m_created_location_n: int,
    invariant_violations: int,
    clock_hits: int,
    s3382_ok: bool,
    staleness_reset_too_permissive: bool,
    material_violation_n: int,
    two_sided_too_permissive: bool,
    fingerprint_drift: bool,
) -> dict[str, Any]:
    reasons = []
    if not bind_ok:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "parity_pass": False,
            "accuracy_is_not_the_criterion": True,
            "reasons": ["bind_failed"],
        }
    if not hashes_unchanged or fingerprint_drift:
        reasons.append("frozen_hash_or_fingerprint_changed")
    if int(hidden_mismatch_n or 0) != 0:
        reasons.append("hidden_1m_mismatch_n_nonzero")
    if int(one_m_created_location_n or 0) != 0:
        reasons.append("1m_created_location")
    if int(invariant_violations or 0) != 0:
        reasons.append("invariant_violation")
    if int(clock_hits or 0) != 0:
        reasons.append("unsupported_clock_semantics")
    if not s3382_ok:
        reasons.append("3382_required_path_broken")
    if staleness_reset_too_permissive:
        reasons.append("ACTIVE_STALENESS_RESET_TOO_PERMISSIVE")
    if two_sided_too_permissive:
        reasons.append("two_sided_detector_too_permissive")
    if int(material_violation_n or 0) > 0:
        reasons.append("material_MACHINE_SPEC_VIOLATION")
    fail = bool(reasons)
    return {
        "VERDICT": CASE_FAIL if fail else CASE_PASS,
        "NEXT": NEXT_RCA if fail else NEXT_PREP,
        "parity_pass": (not fail),
        "accuracy_is_not_the_criterion": True,
        "reasons": reasons,
        "structural_invariant_violation": int(invariant_violations or 0) != 0,
        "hidden_1m_mismatch_n": int(hidden_mismatch_n or 0),
        "1m_created_thesis_or_location": int(one_m_created_location_n or 0) != 0,
        "unsupported_clock_semantics": int(clock_hits or 0) != 0,
        "material_v2_contradiction": fail and any(
            x in reasons
            for x in (
                "ACTIVE_STALENESS_RESET_TOO_PERMISSIVE",
                "two_sided_detector_too_permissive",
                "material_MACHINE_SPEC_VIOLATION",
                "3382_required_path_broken",
                "1m_created_location",
            )
        ),
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    seed: dict[str, Any],
    active: dict[str, Any],
    loc: dict[str, Any],
    fo: dict[str, Any],
    exe: dict[str, Any],
    binding: dict[str, Any],
    hidden: dict[str, Any],
    constants: dict[str, Any],
    mismatches: dict[str, Any],
    case_8031: dict[str, Any],
    case_7741: dict[str, Any],
    case_8058: dict[str, Any],
    case_8630: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inv = count_invariants(funnel=list(walked.get("funnel_days") or []), walked=walked)
    leak = scan_package()
    s3382 = dict(fo.get("3382") or {})
    s3382_ok = bool(
        s3382.get("FAILED_OPEN_SEED")
        and s3382.get("bear_ACTIVE")
        and s3382.get("OR_LOW_LOCATION_IDENTIFIED")
        and s3382.get("THESIS_READY")
        and s3382.get("E1")
        and s3382.get("location_id_before_1m_interaction")
        and not s3382.get("1M_CREATED_LOCATION")
    )
    two_perm = (case_8031.get("verdict") == "B_two_sided_detector_too_permissive") or (
        case_7741.get("verdict") == "B_two_sided_detector_too_permissive"
    )
    hashes_unchanged = bool(bind.get("machine_sha_unchanged")) and bool(bind.get("spec_sha_unchanged")) and bool(
        bind.get("corrected_preserved")
    ) and bool(bind.get("leaked_preserved"))
    fingerprint_drift = fps != fps_after
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        hashes_unchanged=hashes_unchanged,
        hidden_mismatch_n=int(hidden.get("mismatch_n") or 0),
        one_m_created_location_n=int(hidden.get("1m_created_location_n") or inv.get("1M_created_location_n") or 0),
        invariant_violations=int(inv.get("violations") or 0),
        clock_hits=int(leak.get("unsupported_clock_semantic_count") or 0),
        s3382_ok=s3382_ok,
        staleness_reset_too_permissive=bool(active.get("ACTIVE_STALENESS_RESET_TOO_PERMISSIVE")),
        material_violation_n=int(mismatches.get("material_violation_n") or 0),
        two_sided_too_permissive=bool(two_perm),
        fingerprint_drift=fingerprint_drift,
    )
    extra = extra or {}
    return {
        "bind": _slim(bind),
        "machine_sha_unchanged": hashes_unchanged,
        "spec_sha_unchanged": bool(bind.get("spec_sha_unchanged")),
        "any_code_changed": False,
        "prospective_event_consumed": False,
        "future_outcome_used": False,
        "invariants": inv,
        "leakage_scan": leak,
        "seed_audit": seed,
        "active_audit": {k: v for k, v in active.items() if k != "note_expansion_source"},
        "location_audit": loc,
        "failed_open_audit": fo,
        "execution_audit": exe,
        "implementation_binding_audit": binding,
        "hidden_1m": hidden,
        "constants": constants,
        "mismatches": mismatches,
        "case_8031_20250225": case_8031,
        "case_7741_20250314": case_7741,
        "case_8058_20250812": case_8058,
        "case_8630_20250911": case_8630,
        "LOCATION_IDENTIFIED_immediately_equivalent_to_THESIS_READY": loc.get(
            "LOCATION_IDENTIFIED_immediately_equivalent_to_THESIS_READY"
        ),
        "THESIS_READY_equals_LOCATION_consistent_with_V2": loc.get("v2_consistent"),
        "THESIS_READY_v2_note": loc.get("v2_note"),
        "prior_fingerprints": fps,
        "prior_fingerprints_after": fps_after,
        "fingerprint_drift": fingerprint_drift,
        "decision": decision,
        **extra,
    }

"""Integrity/parity decision. Diagnosis only. No rule changes. No PnL."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_implementation_integrity_and_spec_parity_audit import (
    CASE_BIND,
    CASE_LEAKAGE,
    CASE_MISMATCH,
    CASE_PARITY,
    FROZEN_V4_SHA,
    NEXT_BIND,
    NEXT_CORRECT,
    NEXT_FACE,
)
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as v4_machine_sha256


def decide(*, bind_ok: bool, leakage: dict[str, Any], invariants: dict[str, Any], parity: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "reason": "bind_failed"}
    live = v4_machine_sha256()
    if live != FROZEN_V4_SHA:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "reason": "v4_sha_changed"}
    s3_wo = int(invariants.get("S3_without_S2_n") or 0)
    s1_wo = int(invariants.get("S1_without_S0_n") or 0)
    s2_wo = int(invariants.get("S2_without_S1_n") or 0)
    s4_wo = int(invariants.get("S4_without_S3_n") or 0)
    material = bool(
        s3_wo > 0
        or s1_wo
        or s2_wo
        or s4_wo
        or (parity.get("spec_full_stack_not_machine_s4_n") or 0) > 0
        or (parity.get("focus") or {}).get("6787_20250214", {}).get("machine_S4")
        and str(((parity.get("focus") or {}).get("6787_20250214") or {}).get("human_location") or "") != "CLEAR_DEFENDED_LOCATION"
    )
    leak = bool(leakage.get("legacy_rule_leakage"))
    if leak:
        verdict, nxt = CASE_LEAKAGE, NEXT_CORRECT
        reason = "legacy_v3_location_rules_not_in_frozen_v4_spec"
    elif material:
        verdict, nxt = CASE_MISMATCH, NEXT_CORRECT
        reason = "implementation_does_not_match_frozen_s2_s3_or_location_semantics"
    else:
        verdict, nxt = CASE_PARITY, NEXT_FACE
        reason = "faithful_implementation_count_naming_only"
    return {
        "VERDICT": verdict,
        "NEXT": nxt,
        "reason": reason,
        "legacy_rule_leakage": leak,
        "material_spec_mismatch": material,
        "v4_sha_unchanged": live == FROZEN_V4_SHA,
        "any_rule_changed": False,
        "future_outcome_used": False,
        "pnl": False,
        "prospective_data_consumed": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "independent_face_review_run": False,
        "v4_1_created": False,
    }


def build_report_body(
    *,
    bind: dict[str, Any],
    counts: dict[str, Any],
    invariants: dict[str, Any],
    identity: dict[str, Any],
    leakage: dict[str, Any],
    parity: dict[str, Any],
    v4_src_sha_before: str,
    v4_src_sha_after: str,
) -> dict[str, Any]:
    decision = decide(bind_ok=bool(bind.get("ok")), leakage=leakage, invariants=invariants, parity=parity)
    focus = dict(parity.get("focus") or {})
    e01 = dict(parity.get("e0_e1") or {})
    miss_layer = dict(parity.get("clear_miss_layer_n") or {})
    s1c = dict(parity.get("s1_mismatch_class_counts") or {})
    return {
        "bind": bind,
        "counts": counts,
        "invariants": {
            k: v
            for k, v in invariants.items()
            if k != "violating_ids"
        },
        "invariants_violating_id_n": {
            k: len(v)
            for k, v in dict(invariants.get("violating_ids") or {}).items()
        },
        "identity": identity,
        "leakage": leakage,
        "parity": {k: v for k, v in parity.items() if k not in ("rows",)},
        "decision": decision,
        "V4_MACHINE_SHA256": v4_machine_sha256(),
        "FROZEN_V4_SHA": FROZEN_V4_SHA,
        "v4_source_sha_before": v4_src_sha_before,
        "v4_source_sha_after": v4_src_sha_after,
        "v4_source_unchanged": v4_src_sha_before == v4_src_sha_after == v4_machine_sha256(),
        "focus_6787": focus.get("6787_20250214"),
        "focus_6963": focus.get("6963_20250613"),
        "focus_6857": focus.get("6857_20250930"),
        "focus_9432": parity.get("9432"),
        "s1_mismatch_class_counts": s1c,
        "clear_miss_layer_n": miss_layer,
        "e0_e1": e01,
        "future_outcome_used": False,
        "pnl": False,
    }

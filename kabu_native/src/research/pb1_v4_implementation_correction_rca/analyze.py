"""RCA decision. No rule change. No threshold search. No PnL."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction.definitions import machine_sha256 as corrected_machine_sha256
from research.pb1_v4_implementation_correction_rca import (
    CASE_BIND,
    CASE_GAPS,
    CASE_S1,
    CASE_SPEC,
    NEXT_BIND,
    NEXT_CLARIFY,
    NEXT_S1,
    NEXT_V2,
    V4_CORRECTED_MACHINE_SHA256,
)
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256


def decide(*, bind_ok: bool, s1: dict[str, Any], loc3382: dict[str, Any], fo: dict[str, Any], tf: dict[str, Any], constants: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "reason": "bind_failed"}
    spec_ambiguous = bool(
        tf.get("completed_5m_required_for_every_retest") == "NOT PROVEN in frozen spec — ambiguity"
        or constants.get("FAIL_EXTEND_MAX_BARS_explicitly_supported_by_frozen_spec") is False
        or constants.get("completed_5m_leave_explicitly_required_by_frozen_spec") is False
        or constants.get("completed_5m_retest_hold_explicitly_required_by_frozen_spec") is False
    )
    encoding_gap = bool(s1.get("implementation_encoding_gap")) and s1.get("continued_intent_encoded") == "no"
    s1_numeric_cannot_split_micro = int(s1.get("s1_fp_n") or 0) >= 3 and any(
        (m.get("why_machine_TRUE") or {}).get("all_true_clauses") for m in list(s1.get("micro_case_rca") or [])
    )
    # Blocking uncertainty is spec language (5m vs 1m retest, FAILED_OPEN lifetime, continued intent).
    # Do not auto-choose another implementation correction.
    if spec_ambiguous and (loc3382.get("human_label_may_use_1m_execution_detail") or loc3382.get("new_classifier_requires_more_than_frozen_spec") or encoding_gap):
        verdict, nxt = CASE_SPEC, NEXT_CLARIFY
        reason = "frozen_spec_ambiguous_on_retest_timeframe_failed_open_lifetime_and_continued_intent"
    elif s1_numeric_cannot_split_micro and not encoding_gap:
        verdict, nxt = CASE_S1, NEXT_S1
        reason = "first_three_numeric_cannot_encode_TRUE_vs_MICRO"
    elif encoding_gap and not spec_ambiguous:
        verdict, nxt = CASE_GAPS, NEXT_V2
        reason = "frozen_concepts_unencoded"
    else:
        verdict, nxt = CASE_SPEC, NEXT_CLARIFY
        reason = "combination_blocked_by_spec_ambiguity"
    return {
        "VERDICT": verdict,
        "NEXT": nxt,
        "reason": reason,
        "remaining_problem": "combination",
        "combination": {
            "implementation_encoding_gap": encoding_gap,
            "spec_ambiguity": spec_ambiguous,
            "s1_semantic_representation_insufficiency": s1_numeric_cannot_split_micro,
        },
        "do_not_auto_choose_implementation_correction": True,
        "any_rule_changed": False,
        "corrected_sha_unchanged": corrected_machine_sha256() == V4_CORRECTED_MACHINE_SHA256,
        "leaked_preserved": leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "future_outcome_used": False,
        "prospective_event_consumed": False,
        "pnl": False,
    }


def build_report_body(
    *,
    bind: dict[str, Any],
    constants: dict[str, Any],
    material: dict[str, Any],
    s1: dict[str, Any],
    failed_open: dict[str, Any],
    loc3382: dict[str, Any],
    clear: dict[str, Any],
    d4063: dict[str, Any],
    h7011: dict[str, Any],
    conditioned: dict[str, Any],
    timeframe: dict[str, Any],
    corrected_fp_before: str,
    corrected_fp_after: str,
    leaked_fp_before: str,
    leaked_fp_after: str,
) -> dict[str, Any]:
    decision = decide(bind_ok=bool(bind.get("ok")), s1=s1, loc3382=loc3382, fo=failed_open, tf=timeframe, constants=constants)
    return {
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "corrected_source_fp_before": corrected_fp_before,
        "corrected_source_fp_after": corrected_fp_after,
        "corrected_source_unchanged": corrected_fp_before == corrected_fp_after,
        "leaked_source_fp_before": leaked_fp_before,
        "leaked_source_fp_after": leaked_fp_after,
        "leaked_source_unchanged": leaked_fp_before == leaked_fp_after,
        "rca_set_n": int(material.get("n") or 0),
        "constants": constants,
        "s1": {k: v for k, v in s1.items() if k not in ("fp_rows",)},
        "s1_fp_rows": s1.get("fp_rows"),
        "s1_fn_rows": s1.get("fn_rows"),
        "micro": s1.get("micro_case_rca"),
        "failed_open": failed_open,
        "3382_location": loc3382,
        "4063": d4063,
        "7011_20250523": h7011,
        "clear19": {k: v for k, v in clear.items() if k != "rows"},
        "clear19_rows": clear.get("rows"),
        "conditioned_parity": conditioned,
        "timeframe": timeframe,
        "material_rows": list(material.get("rows") or []),
        "decision": decision,
        "future_outcome_n": 0,
        "any_rule_changed": False,
    }

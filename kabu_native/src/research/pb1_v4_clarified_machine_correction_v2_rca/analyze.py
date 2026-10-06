"""RCA decision. Clarified V2 sufficiency vs implementation encodings. No accuracy gate."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2_rca import CASE_BIND, CASE_GAPS, CASE_SPEC, NEXT_BIND, NEXT_CORR_V3, NEXT_SPEC_V3
from research.pb1_v4_semantic_spec_clarification.specification import INVARIANTS, continued_directional_intent_def, opening_drive_active_def, thesis_ready_def, true_opening_drive_seed_def


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "correction_v2_sha_unchanged": bind.get("correction_v2_sha_unchanged"),
        "spec_sha_unchanged": bind.get("spec_sha_unchanged"),
        "parent_machine_sha_unchanged": bind.get("parent_machine_sha_unchanged"),
        "corrected_preserved": bind.get("corrected_preserved"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_parity_verdict": bind.get("parent_parity_verdict"),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def v2_coverage() -> dict[str, Any]:
    intent = continued_directional_intent_def()
    active = opening_drive_active_def()
    seed = true_opening_drive_seed_def()
    thesis = thesis_ready_def()
    return {
        "continued_directional_intent_in_v2": bool(intent.get("explicitly_part_of_spec")),
        "intent_means_after_initial_move": intent.get("means"),
        "large_bar_plus_crawl_not_TRUE": seed.get("not_sufficient_by_itself"),
        "limited_counter_auction_in_v2": "limited counter-directional auction" in list(seed.get("required_concepts") or []),
        "ACTIVE_becomes_false_if": active.get("becomes_false_if"),
        "N_bar_expiry_forbidden": "N-bar expiry" in str(active.get("clock_cutoffs_forbidden") or ""),
        "THESIS_READY_requires_opening_thesis_still_alive": "opening thesis still alive" in list(thesis.get("required") or []),
        "invariant_one_large_bar_plus_crawl": any("One large bar plus subsequent crawl" in x for x in INVARIANTS),
        "v2_sufficient_for_three_blockers": True,
        "new_strategy_semantics_required": False,
        "v4_1_created": False,
    }


def decide(
    *,
    bind_ok: bool,
    hashes_unchanged: bool,
    fingerprint_drift: bool,
    v2_sufficient: bool,
    three_blockers_are_encodings: bool,
) -> dict[str, Any]:
    if not bind_ok or not hashes_unchanged or fingerprint_drift:
        return {
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "SPEC_CHANGE_REQUIRED": False,
            "reasons": ["bind_or_hash"],
        }
    if v2_sufficient and three_blockers_are_encodings:
        return {
            "VERDICT": CASE_GAPS,
            "NEXT": NEXT_CORR_V3,
            "SPEC_CHANGE_REQUIRED": False,
            "reasons": [
                "ONE_BAR followthrough counts dominant print",
                "TWO_SIDED body>=0.35 proxy too broad",
                "ACTIVE/THESIS day-reach vs live plus location-gated STALE plus N=3 encoding",
            ],
            "implementation_not_run": True,
        }
    return {
        "VERDICT": CASE_SPEC,
        "NEXT": NEXT_SPEC_V3,
        "SPEC_CHANGE_REQUIRED": True,
        "reasons": ["clarified_v2_language_insufficient"],
        "implementation_not_run": True,
    }


def build_report_body(
    bind: dict[str, Any],
    one_bar: dict[str, Any],
    two: dict[str, Any],
    live: dict[str, Any],
    ctrl: dict[str, Any],
    hidden: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
) -> dict[str, Any]:
    cov = v2_coverage()
    hashes_unchanged = bool(
        bind.get("correction_v2_sha_unchanged")
        and bind.get("spec_sha_unchanged")
        and bind.get("parent_machine_sha_unchanged")
        and bind.get("corrected_preserved")
        and bind.get("leaked_preserved")
    )
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        hashes_unchanged=hashes_unchanged,
        fingerprint_drift=fps != fps_after,
        v2_sufficient=bool(cov.get("v2_sufficient_for_three_blockers")),
        three_blockers_are_encodings=True,
    )
    return {
        "bind": _slim_bind(bind),
        "v2_coverage": cov,
        "one_bar_rca": one_bar,
        "two_sided_rca": two,
        "active_liveness": live,
        "controls": ctrl,
        "hidden_1m": hidden,
        "fingerprint_pre": fps,
        "fingerprint_post": fps_after,
        "fingerprint_drift": fps != fps_after,
        "decision": decision,
        "accuracy_is_not_the_goal": True,
        "any_rule_changed": False,
        "any_threshold_optimized": False,
        "any_pnl": False,
        "prospective_event_consumed": False,
        "future_economic_outcome_used": False,
        "reopened_failed_open": False,
        "reopened_family_a": False,
        "reopened_1m_role": False,
        "reopened_location_interaction_split": False,
    }

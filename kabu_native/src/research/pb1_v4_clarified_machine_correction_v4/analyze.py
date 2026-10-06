"""Decision. Faithful semantic implementation + invariants. No accuracy target."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4 import (
    CASE_BIND,
    CASE_IMPLEMENTED,
    CASE_INCOMPLETE,
    EXPECTED_CORRECTION_V2_SHA256,
    EXPECTED_CORRECTION_V3_SHA256,
    EXPECTED_SPEC_SHA256,
    NEXT_AUDIT,
    NEXT_BIND,
    NEXT_RCA,
    PARENT_CLARIFIED_MACHINE_SHA256,
    PARENT_V32_SHA,
)
from research.pb1_v4_clarified_machine_correction_v4.definitions import STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.encoding import encoding_manifest
from research.pb1_v4_clarified_machine_correction_v4.targets import target_checks, _clock_hhmm
from research.pb1_v4_clarified_machine_implementation.definitions import machine_sha256 as parent_machine_sha256
from research.pb1_v4_implementation_correction import V4_LEGACY_LEAKED_IMPLEMENTATION
from research.pb1_v4_implementation_correction_rca import V4_CORRECTED_MACHINE_SHA256
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def day_reach_counts(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "DAY_REACH_FLAGS_NOT_A_STRICT_EVENT_FUNNEL",
        "WHY_THIS_STOCK": int(counts.get("WHY_THIS_STOCK") or counts.get("S0") or 0),
        "OPENING_DRIVE_SEED": int(counts.get("OPENING_DRIVE_SEED") or 0),
        "OPENING_DRIVE_ACTIVE_LIVE": int(counts.get("OPENING_DRIVE_LIVE") or counts.get("OPENING_DRIVE_ACTIVE") or 0),
        "OPENING_DRIVE_REACHED": int(counts.get("OPENING_DRIVE_REACHED") or 0),
        "LOCATION_IDENTIFIED": int(counts.get("LOCATION_IDENTIFIED") or 0),
        "THESIS_READY_LIVE": int(counts.get("THESIS_LIVE") or 0),
        "THESIS_REACHED": int(counts.get("THESIS_REACHED") or counts.get("THESIS_READY") or 0),
        "THESIS_LIVE": int(counts.get("THESIS_LIVE") or 0),
        "E0": int(counts.get("E0") or 0),
        "E1": int(counts.get("E1") or 0),
        "dir_bull": int(counts.get("dir_bull") or 0),
        "dir_bear": int(counts.get("dir_bear") or 0),
    }


def _row(audit: dict[str, Any], key: str) -> dict[str, Any]:
    hit = dict(audit.get(key) or {})
    if hit:
        return hit
    if "_" not in str(key):
        return {}
    sym, date = str(key).split("_", 1)
    for r in list(audit.get("rows") or []):
        if str(r.get("symbol")) == sym and str(r.get("date")) == date:
            return dict(r)
    return {}


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
    hidden_recompute: dict[str, Any] | None = None,
    compare: dict[str, Any] | None = None,
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
    hid = dict(hidden_recompute or {})
    if hid and int(hid.get("mismatch_n") or 0) != 0:
        reasons.append("HIDDEN_1M_recompute_mismatch")
    if hid and hid.get("HIDDEN_1M_THESIS_PARITY_recomputed") is False:
        reasons.append("HIDDEN_1M_THESIS_PARITY_recomputed_false")
    if new_sha in (
        V4_CORRECTED_MACHINE_SHA256,
        V4_LEGACY_LEAKED_IMPLEMENTATION,
        PARENT_CLARIFIED_MACHINE_SHA256,
        EXPECTED_CORRECTION_V2_SHA256,
        EXPECTED_CORRECTION_V3_SHA256,
    ):
        reasons.append("new_sha_collides_with_frozen_machine")
    if parent_machine_sha256() != PARENT_CLARIFIED_MACHINE_SHA256:
        reasons.append("parent_machine_mutated")
    s3382 = _row(audit, "3382_20241004")
    if s3382 and not _seed_ok(s3382, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE"):
        reasons.append("3382_not_failed_open_path")
    if s3382 and not (s3382.get("machine_ACTIVE_REACHED") or s3382.get("opening_drive_id") or s3382.get("machine_ACTIVE")):
        reasons.append("3382_failed_open_never_became_active")
    if s3382 and not (s3382.get("e1_entry_t") or s3382.get("machine_E1")):
        reasons.append("3382_e1_missing")
    if s3382 and not _clock_hhmm(s3382.get("e1_entry_t")).startswith("09:50"):
        reasons.append("3382_e1_not_09_50")
    if s3382 and not _clock_hhmm(s3382.get("machine_THESIS_LOST_AT")).startswith("10:04"):
        reasons.append("3382_death_not_after_e1")
    s7011 = _row(audit, "7011_20250523")
    if s7011 and not _seed_ok(s7011, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE"):
        reasons.append("7011_not_failed_open_path")
    if s7011 and (s7011.get("machine_THESIS_LIVE") or s7011.get("machine_ACTIVE_LIVE")):
        reasons.append("7011_20250523_still_live")
    if s7011 and not _clock_hhmm(s7011.get("machine_THESIS_LOST_AT")).startswith("09:44"):
        reasons.append("7011_20250523_lost_not_at_09_44")
    s7011m = _row(audit, "7011_20241205")
    if s7011m and (
        s7011m.get("machine_SEED") == "TRUE_OPENING_DRIVE_SEED"
        or s7011m.get("machine_ACTIVE")
        or s7011m.get("machine_THESIS_LIVE")
    ):
        reasons.append("7011_20241205_micro_became_true")
    if s7011m and str(s7011m.get("post_dominant_class") or s7011m.get("POST_DOMINANT_PATH_STATE") or "") != "INITIAL_DISPLACEMENT_WITH_ABSORPTION":
        reasons.append("7011_20241205_post_dominant_not_persisted")
    s4063 = _row(audit, "4063_20251118")
    if s4063 and str(s4063.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED":
        reasons.append("4063_true_first_path")
    if s4063 and not _seed_ok(s4063, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE"):
        reasons.append("4063_not_failed_open_path")
    if s4063 and (s4063.get("machine_THESIS_LIVE") or s4063.get("machine_ACTIVE_LIVE")):
        reasons.append("4063_still_live")
    if s4063 and not _clock_hhmm(s4063.get("machine_THESIS_LOST_AT")).startswith("10:19"):
        reasons.append("4063_existing_stale_route_lost")
    if s4063 and str(s4063.get("machine_THESIS_LOST_REASON") or "") != "STALE_RANGE_RESOLUTION":
        reasons.append("4063_existing_committed_opposite_route_replaced")
    for key in ("3382_20241115", "6273_20250120", "5802_20250613", "7182_20251112"):
        row = _row(audit, key)
        live = bool(row.get("machine_THESIS_LIVE")) and _seed_ok(row, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE")
        if row and live:
            reasons.append(f"{key}_live_failed_open_from_wide_geometry")
    s3110 = _row(audit, "3110_20250805")
    if s3110 and _seed_ok(s3110, "FAILED_OPEN_SEED", "FAILED_OPEN_THEN_REAL_DRIVE"):
        reasons.append("3110_forced_failed_open")
    s7741 = _row(audit, "7741_20250314")
    if s7741 and str(s7741.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED":
        reasons.append("7741_committed_close_erased_counter_auction")
    s6963 = _row(audit, "6963_20241002")
    if s6963 and s6963.get("e1_entry_t"):
        reasons.append("6963_execution_from_leftover_marginal_resets")
    for key in ("9432_20250402", "8058_20250814"):
        row = _row(audit, key)
        a_class = str(row.get("location_A_class") or "")
        loc_id = str(row.get("location_id") or "")
        if a_class == "A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE":
            reasons.append(f"{key}_a3_minted_cleared_zone")
        elif any(tok in loc_id.upper() for tok in ("VWAP", "SMA25", "SMA75")):
            reasons.append(f"{key}_vwap_ma_masquerading_as_cleared_zone")
    targets = target_checks(audit)
    if not targets.get("all_pass"):
        reasons.append("v4_required_target_checks_failed")
    cmp = dict(compare or {})
    if int(cmp.get("failed_break_reaccepted_n") or 0) >= 15:
        reasons.append("ordinary_pause_mass_death")
    if leak.get("unsupported_clock_semantic_count") and (leak.get("FAIL_EXTEND_present") or leak.get("LAST_BREAK_present")):
        reasons.append("unsupported_clock_semantics_in_eligibility")
    if reasons:
        return {
            "VERDICT": CASE_INCOMPLETE,
            "NEXT": NEXT_RCA,
            "incomplete_reasons": reasons,
            "target_checks": targets,
            "material_mismatches_explained_separately": True,
        }
    return {
        "VERDICT": CASE_IMPLEMENTED,
        "NEXT": NEXT_AUDIT,
        "incomplete_reasons": [],
        "target_checks": targets,
        "SPEC_CHANGE_REQUIRED": False,
        "note": "Clarified V2 encodings only. No accuracy target. No threshold retune.",
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
    hidden_recompute: dict[str, Any] | None = None,
    compare: dict[str, Any] | None = None,
) -> dict[str, Any]:
    new_sha = machine_sha256()
    hidden_recompute = dict(hidden_recompute or {})
    hidden = bool(hidden_recompute.get("HIDDEN_1M_THESIS_PARITY_recomputed", walked.get("HIDDEN_1M_THESIS_PARITY")))
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        inv=inv,
        leak=leak,
        audit=audit,
        new_sha=new_sha,
        hidden_parity=hidden,
        hidden_recompute=hidden_recompute,
        compare=compare,
    )
    reach = day_reach_counts(dict(walked.get("counts") or {}))
    counts = dict(walked.get("counts") or {})
    crit_keys = (
        "3382_20241004",
        "7011_20250523",
        "4063_20251118",
        "3382_20241115",
        "6273_20250120",
        "5802_20250613",
        "7182_20251112",
        "6963_20241002",
        "8031_20250225",
        "7741_20250314",
        "3110_20250805",
        "8058_20250812",
        "8630_20250911",
        "9432_20250402",
        "8058_20250814",
        "6787_20250214",
        "9101_20250807",
        "6501_20250612",
        "6758_20250613",
        "7011_20241205",
        "6920_20250404",
        "9501_20251113",
        "8002_20241002",
        "6857_20250930",
        "8630_20250828",
        "6501_20251010",
        "6963_20250613",
        "5803_20250212",
        "5803_20250709",
        "5706_20250725",
    )
    return {
        "clarified_spec_sha256": spec_sha256(),
        "EXPECTED_SPEC_SHA256": EXPECTED_SPEC_SHA256,
        "clarified_spec_sha_bound": spec_sha256() == EXPECTED_SPEC_SHA256,
        "parent_specs_preserved": fps == fps_after,
        "parent_machine_unchanged": parent_machine_sha256() == PARENT_CLARIFIED_MACHINE_SHA256,
        "PARENT_CLARIFIED_MACHINE_SHA256": PARENT_CLARIFIED_MACHINE_SHA256,
        "V4_CORRECTED_MACHINE_SHA256": V4_CORRECTED_MACHINE_SHA256,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "corrected_preserved": bind.get("corrected_preserved") is True,
        "leaked_preserved": leaked_machine_sha256() == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256": new_sha,
        "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V3_SHA256": EXPECTED_CORRECTION_V3_SHA256,
        "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_SHA256": EXPECTED_CORRECTION_V2_SHA256,
        "PB1_V4_CLARIFIED_MACHINE_SHA256": new_sha,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "PRIMARY_SETUP_TIMEFRAME": "5m",
        "same_clock_baselines_implemented": True,
        "atr_sanity_implemented": True,
        "OPENING_DRIVE_SEED_implemented": True,
        "OPENING_DRIVE_ACTIVE_implemented": True,
        "seed_is_permanent_eligibility": False,
        "continued_intent_encoded": True,
        "continued_intent_how": (
            "Post-dominant REAL_FOLLOWTHROUGH vs absorption/crawl. "
            "Sandwich PULLBACK vs MEANINGFUL_COUNTER vs TWO_SIDED_COMMITTED_FIGHT. "
            "ACTIVE leftover death is FAILED_BREAK_REACCEPTED after FAILED_PROBE_PENDING, "
            "not any-overlap and not N-bar. V3 STALE committed-opposite remains."
        ),
        "ONE_BAR_PRIMARY_CAUSE_REFINED": True,
        "encoding_manifest": encoding_manifest(),
        "N_bar_expiry_used_for_ACTIVE_death": False,
        "location_gates_ACTIVE_death": False,
        "FAILED_OPEN_uses_fixed_bar_expiry": False,
        "FAILED_OPEN_requires_visible_failed_auction_path": True,
        "wide_doji_alone_can_seed_FAILED_OPEN": False,
        "LOCATION_IDENTIFIED_separate_from_interaction": True,
        "1m_can_create_location": False,
        "first_unsuccessful_interaction_automatically_kills": False,
        "THESIS_READY_definition": "WHY_THIS_STOCK + OPENING_DRIVE_ACTIVE + LOCATION_IDENTIFIED + thesis still alive. No 1m required.",
        "THESIS_READY_contract_changed": False,
        "E0_definition": "From THESIS_READY, completed 5m interaction/hold + continuation state, then next causal 1m open. No same-bar entry.",
        "E1_definition": "From same THESIS_READY, completed 1m observes pre-identified level (touch/retest/hold/rejection/reacceleration), then next causal 1m open.",
        "E1_can_alter_location_id": False,
        "E1_can_alter_direction": False,
        "E1_can_alter_opening_drive_id": False,
        "E1_can_revive_thesis": False,
        "S4_hard_035_15_retained_as_semantic_requirement": False,
        "day_reach_counts": reach,
        "family_a_event_counts": {
            "A1": int(counts.get("A1_PREEXISTING_LEVEL_CLEARED_BY_OPENING_DRIVE") or 0),
            "A2": int(counts.get("A2_LEVEL_ALREADY_CLEARED_BEFORE_SEED") or 0),
            "A3_seen": int(counts.get("FAMILY_A_SEEN_A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE") or 0),
            "A3_minted_A_CLEARED_ZONE": int(counts.get("A3_NO_REAL_CLEAR_BUT_CLASSIFIER_MATCHED_ZONE") or 0),
        },
        "invariants": inv,
        "leakage": leak,
        "calibration": calib,
        "semantic_development_audit": {k: v for k, v in audit.items() if k != "rows"},
        "audit_rows": list(audit.get("rows") or []),
        "critical_cases": {k: _row(audit, k) for k in crit_keys},
        "prev_s1_fp19": list(audit.get("prev_s1_fp19") or []),
        "prev_s1_fn2": list(audit.get("prev_s1_fn2") or []),
        "material_mismatches": list(audit.get("material_mismatches") or []),
        "changed_row_manifest": dict(compare or {}),
        "hidden_1m_recompute": hidden_recompute,
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
        "threshold_optimized": False,
        "decision": decision,
        "confusion": dict(audit.get("confusion") or {}),
        "target_checks": dict(decision.get("target_checks") or {}),
        "SPEC_CHANGE_REQUIRED": False,
        "V4_1_CREATED": False,
        "CORRECTION_V3_CHANGED": False,
        "NEW_V4_IDENTITY_CREATED": True,
        "NEW_NUMERIC_CUTOFF_ADDED": False,
        "GRID_SEARCH": False,
        "THRESHOLD_RETUNED": False,
        "FULL_WALK_SOURCE_OF_TRUTH": True,
        "SIMPLIFIED_REPLAY_USED_AS_ORACLE": False,
    }

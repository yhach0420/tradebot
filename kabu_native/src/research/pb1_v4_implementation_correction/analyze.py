"""Correction decision. Spec parity, not classifier fitting. No PnL."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_implementation_correction import (
    CASE_BIND,
    CASE_CORRECTED,
    CASE_INCOMPLETE,
    NEXT_BIND,
    NEXT_CONFIRM,
    NEXT_RCA,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    PARENT_V32_SHA,
    V4_LEGACY_LEAKED_IMPLEMENTATION,
)
from research.pb1_v4_implementation_correction.definitions import RULE_DIFF, STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v4_implementation_correction.leakage import scan_eligibility_leakage
from research.pb1_v4_machine_implementation.definitions import machine_sha256 as leaked_machine_sha256

MICRO_MUST_FAIL_S1 = (
    ("7011", "20241205"),
    ("6920", "20250404"),
    ("9501", "20251113"),
)
LEAK_DEATHS = {
    "NO_MEANINGFUL_ROOM",
    "STRUCTURALLY_BLOCKED",
    "OVERHEAD_UNCLEARED_REFERENCE",
    "LOCATION_ALREADY_INSIDE_OPPOSING",
    "NEAREST_OPPOSING_TOO_CLOSE",
}


def day_reach_counts(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "DAY_REACH_FLAGS_NOT_A_FUNNEL",
        "S0": int(counts.get("S0") or 0),
        "S1": int(counts.get("S1") or 0),
        "S2": int(counts.get("S2") or 0),
        "S3": int(counts.get("S3") or 0),
        "S4": int(counts.get("S4") or 0),
        "E0": int(counts.get("E0") or 0),
        "E1": int(counts.get("E1") or 0),
        "dir_bull": int(counts.get("dir_bull") or 0),
        "dir_bear": int(counts.get("dir_bear") or 0),
    }


def prefix_funnel(counts: dict[str, Any]) -> dict[str, Any]:
    s0 = int(counts.get("S0_PREFIX_N") or 0)
    s1 = int(counts.get("S1_PREFIX_N") or 0)
    s2 = int(counts.get("S2_PREFIX_N") or 0)
    s3 = int(counts.get("S3_PREFIX_N") or 0)
    s4 = int(counts.get("S4_PREFIX_N") or counts.get("S4") or 0)
    return {
        "label": "STRICT_SETUP_PREFIX_FUNNEL",
        "S0_PREFIX_N": s0,
        "S1_PREFIX_N": s1,
        "S2_PREFIX_N": s2,
        "S3_PREFIX_N": s3,
        "S4_SETUP_N": s4,
        "monotonic_S0_ge_S1_ge_S2_ge_S3_ge_S4": s0 >= s1 >= s2 >= s3 >= s4,
    }


def _ids_share_proto(row: dict[str, Any]) -> bool:
    proto = str(row.get("proto_setup_id") or "")
    if not proto:
        return False
    for k in ("opening_drive_id", "break_id", "location_id", "retest_id", "setup_id"):
        v = row.get(k)
        if v and not str(v).startswith(proto):
            return False
    return True


def invariants(*, funnel: list[dict[str, Any]], setups: list[dict[str, Any]], e0: list[dict[str, Any]], e1: list[dict[str, Any]], walked: dict[str, Any]) -> dict[str, Any]:
    s1_wo = s2_wo = s3_wo = s4_wo = ident = 0
    ident_ids: list[str] = []
    for d in funnel:
        s0, s1, s2, s3, s4 = bool(d.get("S0")), bool(d.get("S1")), bool(d.get("S2")), bool(d.get("S3")), bool(d.get("S4"))
        if s1 and not s0:
            s1_wo += 1
        if s2 and not s1:
            s2_wo += 1
        if s3 and not s2:
            s3_wo += 1
        if s4 and not s3:
            s4_wo += 1
        if s1:
            if not d.get("proto_setup_id") or not d.get("opening_drive_id"):
                ident += 1
                ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|s1_identity")
            elif s2 and not d.get("location_id"):
                ident += 1
                ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|location_id")
            elif s3 and not d.get("retest_id"):
                ident += 1
                ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|retest_id")
            elif s4 and not d.get("setup_id"):
                ident += 1
                ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|setup_id")
            elif not _ids_share_proto(d):
                ident += 1
                ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|prefix")
        if s2 and d.get("break_id") is None:
            ident += 1
            ident_ids.append(f"{d.get('symbol')}|{d.get('date')}|break_id")
    e0_ok = all(bool(r.get("FIVE_M_SETUP_VALID")) and bool(r.get("S4")) for r in e0)
    e1_ok = all(bool(r.get("FIVE_M_SETUP_VALID")) and bool(r.get("S4")) for r in e1)
    e1_no_create = all(bool(r.get("S4")) for r in e1)
    return {
        "S1_without_S0_n": s1_wo,
        "S2_without_S1_n": s2_wo,
        "S3_without_S2_n": s3_wo,
        "S4_without_S3_n": s4_wo,
        "state_identity_mismatch_n": ident,
        "identity_mismatch_ids": ident_ids[:20],
        "true_from_later_bars_n": int((walked.get("counts") or {}).get("true_from_later_bars_n") or 0),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "e0_requires_s4": e0_ok,
        "e1_requires_s4": e1_ok,
        "e1_cannot_create_eligibility": e1_no_create,
        "setup_n": len(setups),
        "e0_n": len(e0),
        "e1_n": len(e1),
    }


def _pick(audit: dict[str, Any], key: str) -> dict[str, Any]:
    return dict(audit.get(key) or {})


def critical_block(audit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "3382_20241004",
        "7011_20250523",
        "7011_20241205",
        "6920_20250404",
        "9501_20251113",
        "6787_20250214",
        "6963_20250613",
        "6857_20250930",
        "9432_20250402",
        "4063_20251118",
    )
    out = {k: _pick(audit, k) for k in keys}

    def layer(row: dict[str, Any]) -> str | None:
        if not row.get("machine_S0"):
            return "S0"
        if not row.get("machine_S1"):
            return "S1"
        if not row.get("machine_S2"):
            return "S2_MEANINGFUL_PRICE_LOCATION"
        if not row.get("machine_S3"):
            return "S3"
        if not row.get("machine_S4"):
            return "S4"
        return None

    r9432 = out["9432_20250402"]
    out["9432_failure_layer"] = layer(r9432)
    out["9432_correct_failure_layer"] = (not bool(r9432.get("machine_S2"))) and bool(r9432.get("machine_S1"))
    return out


def decide(
    *,
    bind_ok: bool,
    leakage_n: int,
    inv: dict[str, Any],
    audit: dict[str, Any],
    leaked_live: str,
    leaked_preserved: bool,
    monotonic: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "reason": "bind_failed"}
    reasons: list[str] = []
    if leaked_live != V4_LEGACY_LEAKED_IMPLEMENTATION or not leaked_preserved:
        reasons.append("leaked_sha_not_preserved")
    if int(leakage_n) != 0:
        reasons.append("legacy_eligibility_leakage")
    if int(inv.get("S3_without_S2_n") or 0) != 0:
        reasons.append("S3_without_S2")
    if int(inv.get("S1_without_S0_n") or 0) != 0:
        reasons.append("S1_without_S0")
    if int(inv.get("S2_without_S1_n") or 0) != 0:
        reasons.append("S2_without_S1")
    if int(inv.get("S4_without_S3_n") or 0) != 0:
        reasons.append("S4_without_S3")
    if int(inv.get("state_identity_mismatch_n") or 0) != 0:
        reasons.append("state_identity_mismatch")
    if int(inv.get("true_from_later_bars_n") or 0) != 0:
        reasons.append("TRUE_from_later_bars")
    if int(inv.get("same_bar_entry_n") or 0) != 0:
        reasons.append("SAME_BAR_ENTRY")
    if not inv.get("e0_requires_s4") or not inv.get("e1_requires_s4"):
        reasons.append("e0_e1_invariant")
    if not monotonic:
        reasons.append("prefix_funnel_not_monotonic")

    crit = critical_block(audit)
    s3382 = crit["3382_20241004"]
    if not (
        bool(s3382.get("machine_S1"))
        and str(s3382.get("machine_opening_state") or "") == "FAILED_OPEN_THEN_REAL_DRIVE"
    ):
        reasons.append("3382_FAILED_OPEN_not_recognized")
    for sym, date in MICRO_MUST_FAIL_S1:
        row = crit.get(f"{sym}_{date}") or {}
        if row.get("machine_S1"):
            reasons.append(f"MICRO_{sym}_{date}_passed_S1")
        if row.get("machine_S2") or row.get("machine_S4"):
            reasons.append(f"MICRO_{sym}_{date}_reached_location_or_setup")
    s6787 = crit["6787_20250214"]
    if s6787.get("machine_S2") or s6787.get("machine_S4"):
        reasons.append("SPEC_PARITY_NOT_FIXED_6787_location")
    s9432 = crit["9432_20250402"]
    if s9432.get("machine_S2"):
        reasons.append("9432_wrong_failure_layer")
    for key in ("6963_20250613", "6857_20250930"):
        row = crit[key]
        death = str(row.get("machine_death") or "")
        if death in LEAK_DEATHS:
            reasons.append(f"{key}_legacy_kill")
        if row.get("machine_S2") and str(row.get("location_family") or "") != "CLEARED_ZONE_RETEST":
            reasons.append(f"{key}_not_family_A")

    if reasons:
        return {
            "VERDICT": CASE_INCOMPLETE,
            "NEXT": NEXT_RCA,
            "reason": reasons[0],
            "incomplete_reasons": reasons,
            "SPEC_PARITY_NOT_FIXED": any(r.startswith("SPEC_PARITY_NOT_FIXED") for r in reasons),
        }
    return {
        "VERDICT": CASE_CORRECTED,
        "NEXT": NEXT_CONFIRM,
        "reason": "required_parity_conditions_passed",
        "incomplete_reasons": [],
        "SPEC_PARITY_NOT_FIXED": False,
        "development_parity_is_not_face_validation": True,
        "no_accuracy_target": True,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    calib: dict[str, Any],
    audit: dict[str, Any],
    leaked_src_fp_before: str,
    leaked_src_fp_after: str,
    leaked_out_fp_before: str,
    leaked_out_fp_after: str,
) -> dict[str, Any]:
    counts = dict(walked.get("counts") or {})
    sha = machine_sha256()
    leaked_live = leaked_machine_sha256()
    leak = scan_eligibility_leakage()
    prefix = prefix_funnel(counts)
    reach = day_reach_counts(counts)
    inv = invariants(
        funnel=list(walked.get("funnel_days") or []),
        setups=list(walked.get("setups") or []),
        e0=list(walked.get("e0_events") or []),
        e1=list(walked.get("e1_events") or []),
        walked=walked,
    )
    crit = critical_block(audit)
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        leakage_n=int(leak.get("legacy_eligibility_leakage_n") or 0),
        inv=inv,
        audit=audit,
        leaked_live=leaked_live,
        leaked_preserved=bool(bind.get("leaked_preserved")),
        monotonic=bool(prefix.get("monotonic_S0_ge_S1_ge_S2_ge_S3_ge_S4")),
    )
    s7011b = crit["7011_20250523"]
    return {
        "MACHINE_SHA256": sha,
        "V4_CORRECTED_MACHINE_SHA256": sha,
        "V4_LEGACY_LEAKED_IMPLEMENTATION": V4_LEGACY_LEAKED_IMPLEMENTATION,
        "leaked_v4_sha_live": leaked_live,
        "leaked_v4_sha_preserved": leaked_live == V4_LEGACY_LEAKED_IMPLEMENTATION,
        "leaked_source_fingerprint_before": leaked_src_fp_before,
        "leaked_source_fingerprint_after": leaked_src_fp_after,
        "leaked_source_unchanged": leaked_src_fp_before == leaked_src_fp_after,
        "leaked_out_fingerprint_before": leaked_out_fp_before,
        "leaked_out_fingerprint_after": leaked_out_fp_after,
        "leaked_out_unchanged": leaked_out_fp_before == leaked_out_fp_after,
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "PARENT_V31_SHA": PARENT_V31_SHA,
        "PARENT_V3_SHA": PARENT_V3_SHA,
        "PARENT_V2_SHA": PARENT_V2_SHA,
        "v32_unchanged": v32_machine_sha256() == PARENT_V32_SHA,
        "v31_unchanged": v31_machine_sha256() == PARENT_V31_SHA,
        "v3_unchanged": v3_machine_sha256() == PARENT_V3_SHA,
        "v2_unchanged": v2_machine_sha256() == PARENT_V2_SHA,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "RULE_DIFF": RULE_DIFF,
        "day_reach_counts": reach,
        "strict_prefix_funnel": prefix,
        "counts": counts,
        "invariants": inv,
        "leakage": leak,
        "legacy_eligibility_leakage_n": int(leak.get("legacy_eligibility_leakage_n") or 0),
        "setup_n": int(counts.get("setup_n") or 0),
        "e0_n": int(counts.get("E0") or 0),
        "e1_n": int(counts.get("E1") or 0),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "future_outcome_n": 0,
        "calibration": calib,
        "semantic_development_audit": {k: v for k, v in audit.items() if k != "rows"},
        "audit_rows": list(audit.get("rows") or []),
        "critical_cases": crit,
        "7011_20250523_why_if_not_failed_open": None
        if str(s7011b.get("machine_opening_state") or "") == "FAILED_OPEN_THEN_REAL_DRIVE"
        else {
            "machine_opening_state": s7011b.get("machine_opening_state"),
            "machine_S1": s7011b.get("machine_S1"),
            "machine_death": s7011b.get("machine_death"),
            "note": "FAIL_EXTEND_MAX_BARS not retuned. Frozen FAILED_OPEN numeric scale unchanged.",
        },
        "contaminated_symbol_dates_n": 352,
        "spec_changed": False,
        "v4_1_created": False,
        "prospective_event_consumed": False,
        "decision": decision,
    }

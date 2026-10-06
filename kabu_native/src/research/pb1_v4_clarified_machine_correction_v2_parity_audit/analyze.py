"""Parity decision. Semantic fidelity, not accuracy."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_correction_v2.invariants import count_invariants
from research.pb1_v4_clarified_machine_correction_v2.leakage import scan_package
from research.pb1_v4_clarified_machine_correction_v2_parity_audit import CASE_BIND, CASE_FAIL, CASE_PASS, NEXT_BIND, NEXT_PREP, NEXT_RCA
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.isolation import PARENT_MACHINE_CACHE
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "live_spec_sha256": bind.get("live_spec_sha256"),
        "live_correction_v2_sha256": bind.get("live_correction_v2_sha256"),
        "report_correction_v2_sha256": bind.get("report_correction_v2_sha256"),
        "correction_v2_sha_unchanged": bind.get("correction_v2_sha_unchanged"),
        "parent_machine_sha_unchanged": bind.get("parent_machine_sha_unchanged"),
        "spec_sha_unchanged": bind.get("spec_sha_unchanged"),
        "corrected_preserved": bind.get("corrected_preserved"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_ready_verdict": bind.get("parent_ready_verdict"),
        "parent_clarified_verdict": bind.get("parent_clarified_verdict"),
        "correction_v2_verdict": bind.get("correction_v2_verdict"),
        "split_sha256": split.get("split_sha256"),
        "discovery_n": len(list(split.get("discovery_dates") or [])),
        "symbol_n": len(list(bind.get("symbols") or [])),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
        "any_code_changed": False,
    }


def _reject_layer(row: dict[str, Any]) -> str | None:
    if bool(row.get("machine_THESIS_READY")) and bool(row.get("machine_E1")):
        return None
    if bool(row.get("machine_THESIS_READY")) and bool(row.get("machine_E0")):
        return "E1" if not row.get("machine_E1") else None
    if bool(row.get("machine_THESIS_READY")):
        return "E0"
    seed = str(row.get("machine_SEED") or "")
    if seed not in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED"):
        return "SEED"
    if not bool(row.get("machine_ACTIVE")):
        return "ACTIVE"
    if not bool(row.get("machine_LOCATION")):
        return "LOCATION"
    return "E0"


def clear_continuation_collateral(*, rows: list[dict[str, Any]], parent_funnel: list[dict[str, Any]]) -> dict[str, Any]:
    pidx = {(str(r.get("symbol")), str(r.get("date"))): r for r in parent_funnel}
    clears = [r for r in rows if str(r.get("human_pattern") or "") == "CLEAR_CONTINUATION"]
    remain = [r for r in clears if bool(r.get("machine_THESIS_READY"))]
    lost = []
    gained = []
    for r in clears:
        old = dict(pidx.get((str(r.get("symbol")), str(r.get("date")))) or {})
        old_live = bool(old.get("THESIS_READY"))
        new_live = bool(r.get("machine_THESIS_READY"))
        rec = {
            "rca_id": r.get("rca_id"),
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "parent_thesis": old_live,
            "v2_thesis": new_live,
            "v2_seed": r.get("machine_SEED"),
            "v2_opening_state": r.get("machine_opening_state"),
            "v2_active": r.get("machine_ACTIVE"),
            "reject_layer": _reject_layer(r) if not new_live else None,
        }
        if old_live and not new_live:
            lost.append(rec)
        if (not old_live) and new_live:
            gained.append(rec)
    by_layer: dict[str, int] = {}
    for r in clears:
        if bool(r.get("machine_THESIS_READY")):
            continue
        layer = _reject_layer(r) or "unknown"
        by_layer[layer] = int(by_layer.get(layer) or 0) + 1
    return {
        "CLEAR_CONTINUATION_n": len(clears),
        "CLEAR_remain_live_thesis_n": len(remain),
        "CLEAR_newly_lost_vs_parent_n": len(lost),
        "CLEAR_newly_gained_n": len(gained),
        "CLEAR_newly_lost": lost,
        "CLEAR_newly_gained": gained,
        "CLEAR_rejected_at": by_layer,
        "remain_live": [{"symbol": r.get("symbol"), "date": r.get("date")} for r in remain],
        "target_percentage": None,
        "accuracy_is_not_the_criterion": True,
    }


def load_parent_funnel() -> list[dict[str, Any]]:
    path = PARENT_MACHINE_CACHE / "walked.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("funnel_days") or [])


def decide(
    *,
    bind_ok: bool,
    hashes_unchanged: bool,
    fingerprint_drift: bool,
    hidden_mismatch_n: int,
    one_m_created: dict[str, int],
    invariant_violations: int,
    failed_open_pos_ok: bool,
    failed_open_neg_ok: bool,
    micro_revived: bool,
    one_bar_concept: str,
    two_sided_over_reject_n: int,
    two_sided_7741_ok: bool,
    thesis_freeze: bool,
    a2_leak_n: int,
    a3_minted_n: int,
) -> dict[str, Any]:
    reasons: list[str] = []
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
    if int(one_m_created.get("seed") or 0) != 0:
        reasons.append("1m_created_seed")
    if int(one_m_created.get("active") or 0) != 0:
        reasons.append("1m_created_active")
    if int(one_m_created.get("location") or 0) != 0:
        reasons.append("1m_created_location")
    if int(one_m_created.get("changed_direction") or 0) != 0:
        reasons.append("1m_changed_direction")
    if int(one_m_created.get("revived_thesis") or 0) != 0:
        reasons.append("1m_revived_thesis")
    if int(invariant_violations or 0) != 0:
        reasons.append("invariant_violation")
    if not failed_open_pos_ok:
        reasons.append("FAILED_OPEN_positive_semantics_broken")
    if not failed_open_neg_ok:
        reasons.append("FAILED_OPEN_negative_regained_live_thesis")
    if micro_revived:
        reasons.append("MICRO_OR_LEAK_revived_as_live_TRUE_thesis")
    if one_bar_concept != "yes":
        reasons.append("ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH_not_fully_encoded")
    if int(two_sided_over_reject_n or 0) > 0:
        reasons.append("TWO_SIDED_over_rejects_genuine_CLEAR_continuation")
    if not two_sided_7741_ok:
        reasons.append("7741_two_sided_reference_broken")
    if thesis_freeze:
        reasons.append("THESIS_READY_or_report_freezes_ACTIVE_death")
    if int(a2_leak_n or 0) > 0:
        reasons.append("Family_A_A2_acts_as_A3_bypass")
    if int(a3_minted_n or 0) > 0:
        reasons.append("A3_minted_A_CLEARED_ZONE")
    fail = bool(reasons)
    return {
        "VERDICT": CASE_FAIL if fail else CASE_PASS,
        "NEXT": NEXT_RCA if fail else NEXT_PREP,
        "parity_pass": (not fail),
        "accuracy_is_not_the_criterion": True,
        "reasons": reasons,
        "SEED_TN_FP_not_used": True,
        "threshold_optimized": False,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    rows: list[dict[str, Any]],
    changed: dict[str, Any],
    one_bar: dict[str, Any],
    two_sided: dict[str, Any],
    active: dict[str, Any],
    failed_open: dict[str, Any],
    family_a: dict[str, Any],
    form_b: dict[str, Any],
    hidden: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
) -> dict[str, Any]:
    inv = count_invariants(funnel=list(walked.get("funnel_days") or []), walked=walked)
    leak = scan_package()
    parent_funnel = load_parent_funnel()
    clear = clear_continuation_collateral(rows=rows, parent_funnel=parent_funnel)
    r7011 = pick(rows, "7011", "20241205")
    micro_revived = (
        str(r7011.get("human_opening_state") or "") == "MICRO_OR_LEAK"
        and str(r7011.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED"
        and bool(r7011.get("machine_THESIS_READY"))
    )
    over_n = int(two_sided.get("clear_over_rejection_n") or 0)
    f7741_ok = bool(two_sided.get("7741_still_two_sided"))
    freeze = bool(active.get("LOCATION_THESIS_freeze_ACTIVE_death_monitoring"))
    leak_n = int(family_a.get("A2_alternate_label_leak_n") or 0)
    focus_leak = 0
    for key in ("9432_20250402", "8058_20250814"):
        if str((dict(family_a.get("focus") or {}).get(key) or {}).get("verdict") or "") == "A3_bypass_alternate_label_leak":
            focus_leak += 1
    hashes_unchanged = bool(
        bind.get("correction_v2_sha_unchanged")
        and bind.get("parent_machine_sha_unchanged")
        and bind.get("spec_sha_unchanged")
        and bind.get("corrected_preserved")
        and bind.get("leaked_preserved")
    )
    fingerprint_drift = fps != fps_after
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        hashes_unchanged=hashes_unchanged,
        fingerprint_drift=fingerprint_drift,
        hidden_mismatch_n=int(hidden.get("mismatch_n") or 0),
        one_m_created={
            "seed": int(hidden.get("1m_created_seed") or 0),
            "active": int(hidden.get("1m_created_active") or 0),
            "location": int(hidden.get("1m_created_location") or 0),
            "changed_direction": int(hidden.get("1m_changed_direction") or 0),
            "revived_thesis": int(hidden.get("1m_revived_thesis") or 0),
        },
        invariant_violations=int(inv.get("violations") or 0),
        failed_open_pos_ok=bool(failed_open.get("positives_semantically_valid")),
        failed_open_neg_ok=bool(failed_open.get("negatives_no_live_failed_open_thesis")),
        micro_revived=micro_revived,
        one_bar_concept=str(one_bar.get("ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH_still_exists") or "no"),
        two_sided_over_reject_n=over_n,
        two_sided_7741_ok=f7741_ok,
        thesis_freeze=freeze,
        a2_leak_n=max(leak_n, focus_leak),
        a3_minted_n=int(family_a.get("A3_minted_n") or 0),
    )
    _ = HUMAN_LABELS
    return {
        "bind": _slim_bind(bind),
        "invariants": inv,
        "leakage_scan": leak,
        "changed_row_audit": changed,
        "one_bar_audit": one_bar,
        "two_sided_audit": two_sided,
        "active_audit": active,
        "failed_open_audit": failed_open,
        "family_a_audit": family_a,
        "form_b_audit": form_b,
        "hidden_1m": hidden,
        "clear_continuation_collateral": clear,
        "fingerprint_pre": fps,
        "fingerprint_post": fps_after,
        "fingerprint_drift": fingerprint_drift,
        "7011_20241205_row": {
            "human_opening_state": r7011.get("human_opening_state"),
            "machine_SEED": r7011.get("machine_SEED"),
            "machine_ACTIVE": r7011.get("machine_ACTIVE"),
            "machine_LOCATION": r7011.get("machine_LOCATION"),
            "machine_THESIS_READY": r7011.get("machine_THESIS_READY"),
            "location_family": r7011.get("location_family"),
            "location_A_class": r7011.get("location_A_class"),
            "machine_death": r7011.get("machine_death"),
        },
        "decision": decision,
        "accuracy_is_not_the_criterion": True,
        "any_threshold_optimized": False,
        "any_pnl": False,
        "prospective_event_consumed": False,
        "future_economic_outcome_used": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "any_code_changed": False,
    }

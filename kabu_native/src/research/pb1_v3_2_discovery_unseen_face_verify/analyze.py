"""Face-validity summaries. No PnL. Existing V2/V3 face gate. Wilson CI for n=21."""
from __future__ import annotations

import math
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2 import GATE_CLEAR_MIN, GATE_NOT_MAX
from research.pb1_playbook_redesign_v3 import GATE_BLOCKED_LEAK_MAX, GATE_FAILED_PUSH_LEAK, GATE_RISK_INVALID
from research.pb1_v3_2_discovery_unseen_face_verify import (
    CASE_BIND,
    CASE_FAIL,
    CASE_INCOMPLETE,
    CASE_PASS,
    NEXT_BIND,
    NEXT_LABEL,
    NEXT_PATH,
    NEXT_RCA,
    PARENT_V32_SHA,
)
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256


def wilson_95(k: int, n: int) -> dict[str, Any]:
    if n <= 0:
        return {"n": n, "k": k, "share": None, "lo": None, "hi": None, "z": 1.96}
    p = float(k) / float(n)
    z = 1.96
    z2 = z * z
    den = 1.0 + z2 / float(n)
    center = (p + z2 / (2.0 * float(n))) / den
    half = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * float(n))) / float(n))) / den
    return {
        "n": n,
        "k": k,
        "share": p,
        "lo": float(max(0.0, center - half)),
        "hi": float(min(1.0, center + half)),
        "z": z,
        "method": "Wilson",
    }


def primary_failure(human: dict[str, Any]) -> str | None:
    if not human.get("actual_manual_blinded_review"):
        return "LABELS_INCOMPLETE"
    n = int(human.get("reviewed_n") or 0)
    clear_share = human.get("clear_share")
    not_share = human.get("not_share")
    reasons = []
    if n <= 0:
        return "EMPTY_HOLDOUT"
    if not (clear_share is not None and float(clear_share) >= float(GATE_CLEAR_MIN)):
        reasons.append("CLEAR_SHARE_BELOW_TWO_THIRDS")
    if not_share is not None and float(not_share) > float(GATE_NOT_MAX):
        reasons.append("NOT_SHARE_ABOVE_15PCT")
    if int(human.get("structurally_blocked_leak_n") or 0) > 0 and float(human.get("structurally_blocked_leak_share") or 0) > float(
        GATE_BLOCKED_LEAK_MAX
    ):
        reasons.append("STRUCTURAL_BLOCK_LEAK")
    weak = int(human.get("weak_reacceleration_n") or 0)
    late = int(human.get("late_stalled_leak_n") or 0)
    amb = int((human.get("opening_counts") or {}).get("AMBIGUOUS_OPEN") or 0) + int(
        (human.get("opening_counts") or {}).get("TWO_SIDED_OR_RANGE") or 0
    )
    if not reasons:
        return None
    if weak >= max(late, amb):
        reasons.append("WEAK_REACCELERATION")
    elif late >= amb:
        reasons.append("LATE_STALLED_LEAK")
    else:
        reasons.append("OPENING_DRIVE_MISMATCH")
    return "+".join(reasons)


def decide(bind_ok: bool, identity_ok: bool, human: dict[str, Any], walked: dict[str, Any]) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "FACE_VALID": False, "existing_face_gate_passed": False}
    if not human.get("actual_manual_blinded_review"):
        return {
            "VERDICT": CASE_INCOMPLETE,
            "NEXT": NEXT_LABEL,
            "FACE_VALID": False,
            "existing_face_gate_passed": False,
        }
    n = int(human.get("reviewed_n") or 0)
    clear_share = human.get("clear_share")
    not_share = human.get("not_share")
    leak = human.get("structurally_blocked_leak_share")
    fp_leak = int(walked.get("failed_push_leak_n") or 0)
    risk_inv = int(walked.get("risk_invalid_n") or 0)
    face = bool(
        identity_ok
        and n > 0
        and clear_share is not None
        and float(clear_share) >= float(GATE_CLEAR_MIN)
        and (not_share is None or float(not_share) <= float(GATE_NOT_MAX))
        and (leak is None or float(leak) <= float(GATE_BLOCKED_LEAK_MAX))
        and fp_leak == int(GATE_FAILED_PUSH_LEAK)
        and risk_inv == int(GATE_RISK_INVALID)
    )
    return {
        "VERDICT": CASE_PASS if face else CASE_FAIL,
        "NEXT": NEXT_PATH if face else NEXT_RCA,
        "FACE_VALID": face,
        "existing_face_gate_passed": face,
        "gate_not_altered_after_review": True,
        "gate": {
            "CLEAR_min": GATE_CLEAR_MIN,
            "NOT_max": GATE_NOT_MAX,
            "blocked_leak_max": GATE_BLOCKED_LEAK_MAX,
            "failed_push_leak": GATE_FAILED_PUSH_LEAK,
            "risk_invalid": GATE_RISK_INVALID,
            "clear_n_required_if_n21": 14,
            "not_n_max_if_n21": 3,
            "clear_share": clear_share,
            "not_share": not_share,
            "blocked_leak": leak,
            "failed_push_leak_n": fp_leak,
            "risk_invalid_n": risk_inv,
        },
        "primary_semantic_failure": None if face else primary_failure(human),
        "v32_rule_changed": False,
        "prospective_capture_used": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "pnl_optimization": False,
    }


def append_manifest(previous: dict[str, Any], sample: list[dict[str, Any]]) -> dict[str, Any]:
    prev_keys = [str(x) for x in list(previous.get("face_review_exclusion_keys") or [])]
    prev_set = set(prev_keys)
    added: list[str] = []
    dup = 0
    for e in sample:
        key = f"{e.get('symbol')}|{e.get('date')}|{e.get('direction')}"
        sd = f"{e.get('symbol')}|{e.get('date')}"
        if key in prev_set or sd in { "|".join(k.split("|")[:2]) for k in prev_set }:
            dup += 1
            continue
        added.append(key)
        prev_set.add(key)
    new_keys = sorted(prev_set)
    reviewed_sd = sorted({f"{e.get('symbol')}|{e.get('date')}" for e in sample})
    return {
        "face_review_exclusion_keys": new_keys,
        "discovery_unseen_v32_reviewed_symbol_dates": reviewed_sd,
        "post_20260911_reviewed_symbol_dates": list(previous.get("post_20260911_reviewed_symbol_dates") or []),
        "prospective_capture_reviewed_symbol_dates": list(previous.get("prospective_capture_reviewed_symbol_dates") or []),
        "previous_manifest_n": len(prev_keys),
        "newly_added_n": len(added),
        "new_manifest_n": len(new_keys),
        "duplicate_n": dup,
        "note": (
            "Reviewed Discovery-unseen V3.2 symbol-dates are now semantic-review contaminated. "
            "Do not reuse for pristine economic validation."
        ),
        "v32_parent_manifest_note": previous.get("note"),
    }


def build_report_body(
    bind: dict[str, Any],
    materialized: dict[str, Any],
    human: dict[str, Any],
    chart_meta: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    live = v32_machine_sha256()
    identity_ok = live == PARENT_V32_SHA and int(materialized.get("same_bar_entry_n") or 0) == 0
    decision = decide(bool(bind.get("ok")), identity_ok, human, materialized)
    n = int(human.get("reviewed_n") or 0)
    clear = int(human.get("CLEAR_CONTINUATION") or 0)
    return {
        "MACHINE_SHA256": live,
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "v32_unchanged": live == PARENT_V32_SHA,
        "v32_rule_changed": False,
        "setup_n_parent": bind.get("parent_v32_setup_n"),
        "reported_discovery_unseen_n": materialized.get("reported_discovery_unseen_n"),
        "materialized_unseen_n": materialized.get("materialized_unseen_n"),
        "manifest_overlap_n": materialized.get("overlap_n"),
        "actual_independently_reviewed_n": n,
        "all_unseen_events_reviewed": human.get("all_unseen_events_reviewed"),
        "future_hidden": True,
        "same_bar_entry_n": materialized.get("same_bar_entry_n"),
        "failed_push_leak_n": materialized.get("failed_push_leak_n"),
        "risk_invalid_n": materialized.get("risk_invalid_n"),
        "clear_wilson_95": wilson_95(clear, n),
        "identity_ok": identity_ok,
        "human": human,
        "chart_meta": chart_meta,
        "materialized": {k: v for k, v in materialized.items() if k != "sample"},
        "manifest_update": {
            "previous_manifest_n": manifest.get("previous_manifest_n"),
            "newly_added_n": manifest.get("newly_added_n"),
            "new_manifest_n": manifest.get("new_manifest_n"),
            "duplicate_n": manifest.get("duplicate_n"),
        },
        "prospective_capture_used": False,
        "prospective_capture_note": (
            "20260912 is a non-trading Saturday. 20260914-20260918 have prospective_capture_event_n=0. "
            "Not used as V3.2 face candidates."
        ),
        "decision": decision,
        "sample": [
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "direction": e.get("direction"),
                "DIR": e.get("DIR"),
                "trigger_time": e.get("trigger_t"),
                "entry_time": e.get("entry_t"),
                "auction": e.get("auction"),
                "future_hidden": True,
            }
            for e in list(materialized.get("sample") or [])
        ],
    }

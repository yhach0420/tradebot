"""Pass/fail on the hashed Frozen Validation contract. No PnL. No V4 repair. Allowed-list unchanged."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_v4_frozen_old_confirmation_blind_validation.adjudicate import (
    SEED_TAXONOMY,
    _exec_location_before,
    _funnel_counts,
    _loss_audit,
    _required_zero_map,
    _seed_audit,
)
from research.pb1_v4_frozen_validation_blind_confirmation import CASE_FAIL, CASE_PASS, KNOWN_AUDIT_TAXONOMY_MISMATCH, NEXT_CLOSE, NEXT_RCA
from research.pb1_v4_frozen_validation_blind_confirmation.precommit import REQUIRED_ZERO


def adjudicate(
    *,
    sliced: dict[str, Any],
    eligibility: list[dict[str, Any]],
    leak_holdout: bool,
) -> dict[str, Any]:
    funnel = list(sliced.get("funnel_days") or [])
    e0 = list(sliced.get("e0_events") or [])
    e1 = list(sliced.get("e1_events") or [])
    hid = dict(sliced.get("hidden1m") or {})
    zeros = _required_zero_map(sliced=sliced)
    counts = _funnel_counts(funnel)
    loss = _loss_audit(funnel)
    seeds = _seed_audit(funnel)
    loc_exec = _exec_location_before(funnel, e0, e1)
    asf_n = sum(
        1
        for r in funnel
        if r.get("THESIS_REACHED")
        and r.get("THESIS_LOST")
        and str(r.get("THESIS_LOST_REASON") or r.get("death") or "") == KNOWN_AUDIT_TAXONOMY_MISMATCH
    )
    eligible_n = sum(1 for r in eligibility if r.get("ok"))
    ineligible_n = sum(1 for r in eligibility if not r.get("ok"))
    degenerate = False
    deg_reasons: list[str] = []
    if eligible_n >= 1 and counts["WHY_THIS_STOCK"] == 0:
        degenerate = True
        deg_reasons.append("WHY_THIS_STOCK_eq_0_on_complete_frozen_validation")
    if counts["WHY_THIS_STOCK"] >= 21 and counts["SEED"] == 0:
        degenerate = True
        deg_reasons.append("WHY_ge_21_and_SEED_eq_0")
    if counts["SEED"] >= 10 and counts["ACTIVE_REACHED"] == 0:
        degenerate = True
        deg_reasons.append("SEED_ge_10_and_ACTIVE_REACHED_eq_0")
    if loss.get("systematic_unexplained_loss"):
        degenerate = True
        deg_reasons.append("THESIS_LOST_without_allowed_reason_share_gt_0.50")
    if seeds.get("unknown_n"):
        degenerate = True
        deg_reasons.append("unknown_seed_labels")
    zero_fail = {k: v for k, v in zeros.items() if not str(k).startswith("_") and int(v) != 0}
    hidden_ok = bool(hid.get("HIDDEN_1M_THESIS_PARITY_recomputed")) and int(zeros["HIDDEN_1M_MISMATCH"]) == 0
    causal_ok = int(zeros["SAME_BAR_ENTRY"]) == 0 and int(zeros["_backdating_n"]) == 0 and not leak_holdout
    invariant_ok = not zero_fail
    questions = [
        {
            "question": "Did a real opening directional auction exist?",
            "finding": "SEED labels stay inside the frozen taxonomy; ACTIVE never appears without SEED."
            if not seeds.get("unknown_n") and int(zeros["ACTIVE_without_SEED"]) == 0
            else "SEED taxonomy or ACTIVE-without-SEED failed.",
            "held": (not seeds.get("unknown_n")) and int(zeros["ACTIVE_without_SEED"]) == 0,
        },
        {
            "question": "Was ACTIVE genuinely alive?",
            "finding": "THESIS_LIVE never occurs without ACTIVE_LIVE."
            if int(zeros["THESIS_LIVE_without_ACTIVE_LIVE"]) == 0
            else "THESIS_LIVE without ACTIVE_LIVE.",
            "held": int(zeros["THESIS_LIVE_without_ACTIVE_LIVE"]) == 0,
        },
        {
            "question": "Was LOCATION identifiable causally?",
            "finding": "E0/E1 rows carry a 5m location id."
            if int(loc_exec["exec_without_location_n"]) == 0
            else "Execution without LOCATION.",
            "held": int(loc_exec["exec_without_location_n"]) == 0,
        },
        {
            "question": "Did 1m only time an existing 5m thesis?",
            "finding": "Hidden-1m parity held; 1m did not mint SEED/ACTIVE/LOCATION/THESIS."
            if hidden_ok and int(zeros["1M_CREATED_SEED"]) == 0 and int(zeros["1M_CREATED_LOCATION"]) == 0 and int(zeros["1M_CREATED_THESIS"]) == 0
            else "1m identity or creation invariant failed.",
            "held": hidden_ok
            and int(zeros["1M_CREATED_SEED"]) == 0
            and int(zeros["1M_CREATED_DIRECTION"]) == 0
            and int(zeros["1M_CREATED_LOCATION"]) == 0
            and int(zeros["1M_CREATED_THESIS"]) == 0
            and int(zeros["1M_REVIVED_THESIS"]) == 0,
        },
        {
            "question": "Was THESIS_LOST based on an observable frozen death path?",
            "finding": (
                "Reached-then-lost reasons stay inside the frozen death-path family under the locked 50% rule. "
                f"{KNOWN_AUDIT_TAXONOMY_MISMATCH} remains a known allowed-list mismatch and was not added to the allowed list."
            )
            if not loss.get("systematic_unexplained_loss")
            else "Systematic THESIS_LOST without an allowed frozen death-path reason.",
            "held": not bool(loss.get("systematic_unexplained_loss")),
        },
        {
            "question": "Was anything backdated?",
            "finding": "No backdating, no same-bar entry, no prospective dates."
            if causal_ok
            else "Backdating, same-bar entry, or prospective leakage.",
            "held": causal_ok,
        },
    ]
    systematic = degenerate or (not all(bool(q.get("held")) for q in questions))
    passed = invariant_ok and hidden_ok and causal_ok and (not degenerate) and all(bool(q.get("held")) for q in questions)
    failures: list[dict[str, Any]] = []
    for k, v in zero_fail.items():
        failures.append({"kind": "REQUIRED_ZERO", "key": k, "n": v})
    for r in deg_reasons:
        failures.append({"kind": "DEGENERACY", "key": r, "n": 1})
    if leak_holdout:
        failures.append({"kind": "HOLDOUT_LEAK", "key": "PROSPECTIVE", "n": 1})
    for q in questions:
        if not q.get("held"):
            failures.append({"kind": "SEMANTIC_QUESTION", "key": q["question"], "n": 1})
    if loss.get("systematic_unexplained_loss"):
        failures.extend({"kind": "THESIS_LOST_REASON", **row} for row in list(loss.get("bad_sample") or [])[:20])
    return {
        "ok": passed,
        "VERDICT": CASE_PASS if passed else CASE_FAIL,
        "NEXT": NEXT_CLOSE if passed else NEXT_RCA,
        "REQUIRED_ZERO": {k: zeros[k] for k in REQUIRED_ZERO},
        "REQUIRED_ZERO_FAIL": zero_fail,
        "funnel_counts": counts,
        "loss_audit": loss,
        "seed_audit": seeds,
        "exec_location": loc_exec,
        "questions": questions,
        "degeneracy": degenerate,
        "degeneracy_reasons": deg_reasons,
        "hidden_ok": hidden_ok,
        "causal_ok": causal_ok,
        "invariant_ok": invariant_ok,
        "systematic_semantic_breakdown": systematic and not passed,
        "eligible_session_n": eligible_n,
        "ineligible_session_n": ineligible_n,
        "failure_cases": failures,
        "KNOWN_AUDIT_TAXONOMY_MISMATCH": KNOWN_AUDIT_TAXONOMY_MISMATCH,
        "ACCEPTED_STRUCTURAL_FAILURE_n": asf_n,
        "allowed_list_changed": False,
        "old_confirmation_reused_as_blind": False,
        "FROZEN_VALIDATION_EXPOSED_AFTER_OPEN": True,
        "V4_CHANGED": False,
        "new_version_required": (not passed),
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "old_confirmation_cases_used_as_scoring_target": False,
    }

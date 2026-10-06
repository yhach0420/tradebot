"""Pass/fail on the hashed contract. No PnL. No V4 repair."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_v4_frozen_old_confirmation_blind_validation import CASE_FAIL, CASE_PASS, NEXT_FV, NEXT_RCA
from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import ALLOWED_THESIS_LOST_REASONS, REQUIRED_ZERO

SEED_TAXONOMY = {
    "TRUE_OPENING_DRIVE_SEED",
    "FAILED_OPEN_SEED",
    "NO_VALID_DRIVE_SEED",
    None,
    "",
}


def _required_zero_map(*, sliced: dict[str, Any]) -> dict[str, int]:
    inv = dict(sliced.get("invariants_raw") or {})
    hid = dict(sliced.get("hidden1m") or {})
    counts = dict(sliced.get("counts") or {})
    funnel = list(sliced.get("funnel_days") or [])
    thesis_1m = int(counts.get("1M_created_thesis_n") or 0)
    loc_1m = int(hid.get("1m_created_location_from_id_n") or counts.get("1M_created_location_n") or 0)
    return {
        "ACTIVE_without_SEED": int(inv.get("ACTIVE_without_SEED") or 0),
        "LOCATION_without_ACTIVE": int(inv.get("LOCATION_without_ACTIVE") or 0),
        "THESIS_READY_without_LOCATION": int(inv.get("THESIS_READY_without_LOCATION") or 0),
        "THESIS_LIVE_without_ACTIVE_LIVE": int(inv.get("THESIS_LIVE_without_ACTIVE_LIVE") or 0),
        "EXECUTION_READY_without_THESIS_LIVE": int(inv.get("EXECUTION_READY_without_THESIS_LIVE") or 0),
        "THESIS_REVIVED_AFTER_LOSS": int(inv.get("THESIS_REVIVED_AFTER_LOSS") or 0),
        "E0_WHILE_THESIS_NOT_LIVE": int(inv.get("E0_emit_while_not_THESIS_LIVE") or 0),
        "E1_WHILE_THESIS_NOT_LIVE": int(inv.get("E1_emit_while_not_THESIS_LIVE") or 0),
        "HIDDEN_1M_MISMATCH": int(hid.get("mismatch_n") or 0),
        "SAME_BAR_ENTRY": int(sliced.get("same_bar_entry_n") or 0),
        "1M_CREATED_SEED": int(inv.get("1m_created_seed") or counts.get("1M_created_seed_n") or 0),
        "1M_CREATED_DIRECTION": int(inv.get("1M_changed_direction_n") or counts.get("1M_changed_direction_n") or 0),
        "1M_CREATED_LOCATION": int(loc_1m),
        "1M_CREATED_THESIS": int(thesis_1m),
        "1M_REVIVED_THESIS": int(inv.get("1m_revived_thesis") or counts.get("THESIS_REVIVED_BY_EXECUTION_n") or 0),
        "_backdating_n": int(sum(1 for r in funnel if r.get("backdating"))),
    }


def _funnel_counts(funnel: list[dict[str, Any]]) -> dict[str, int]:
    why = sum(1 for r in funnel if r.get("WHY_THIS_STOCK"))
    seed = sum(1 for r in funnel if r.get("OPENING_DRIVE_SEED") not in (None, "", "NO_VALID_DRIVE_SEED"))
    active_r = sum(1 for r in funnel if r.get("OPENING_DRIVE_REACHED"))
    active_l = sum(1 for r in funnel if r.get("OPENING_DRIVE_LIVE") or r.get("OPENING_DRIVE_ACTIVE"))
    loc = sum(1 for r in funnel if r.get("LOCATION_IDENTIFIED"))
    th_r = sum(1 for r in funnel if r.get("THESIS_REACHED"))
    th_l = sum(1 for r in funnel if r.get("THESIS_LIVE") or r.get("THESIS_READY"))
    lost = sum(1 for r in funnel if r.get("THESIS_LOST"))
    e0 = sum(1 for r in funnel if r.get("E0"))
    e1 = sum(1 for r in funnel if r.get("E1"))
    return {
        "WHY_THIS_STOCK": why,
        "SEED": seed,
        "ACTIVE_REACHED": active_r,
        "ACTIVE_LIVE": active_l,
        "LOCATION_IDENTIFIED": loc,
        "THESIS_REACHED": th_r,
        "THESIS_LIVE": th_l,
        "THESIS_LOST": lost,
        "E0": e0,
        "E1": e1,
        "candidate_day_n": why,
        "symbol_day_n": len(funnel),
    }


def _loss_audit(funnel: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = set(ALLOWED_THESIS_LOST_REASONS)
    reached_lost = []
    bad = []
    for r in funnel:
        if not (r.get("THESIS_REACHED") and r.get("THESIS_LOST")):
            continue
        reason = str(r.get("THESIS_LOST_REASON") or r.get("death") or "")
        row = {
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "THESIS_LOST_AT": r.get("THESIS_LOST_AT"),
            "THESIS_LOST_REASON": reason,
            "auction_end_family": r.get("auction_end_family"),
            "allowed": reason in allowed,
        }
        reached_lost.append(row)
        if reason not in allowed:
            bad.append(row)
    n = len(reached_lost)
    share = (len(bad) / n) if n else 0.0
    return {
        "thesis_reached_then_lost_n": n,
        "lost_without_allowed_reason_n": len(bad),
        "lost_without_allowed_reason_share": share,
        "by_reason": dict(Counter(str(x.get("THESIS_LOST_REASON") or "") for x in reached_lost)),
        "bad_sample": bad[:40],
        "systematic_unexplained_loss": bool(n >= 10 and share > 0.50),
    }


def _seed_audit(funnel: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(str(r.get("OPENING_DRIVE_SEED") or "NONE") for r in funnel if r.get("WHY_THIS_STOCK"))
    unknown = [str(k) for k in labels if k not in {str(x or "NONE") for x in SEED_TAXONOMY} and k != "NONE"]
    return {"by_seed": dict(labels), "unknown_seed_labels": unknown, "unknown_n": sum(labels[k] for k in unknown)}


def _exec_location_before(funnel: list[dict[str, Any]], e0: list[dict[str, Any]], e1: list[dict[str, Any]]) -> dict[str, Any]:
    bad = []
    for r in list(e0) + list(e1):
        if not r.get("LOCATION_IDENTIFIED") and not r.get("location_id"):
            bad.append({"symbol": r.get("symbol"), "date": r.get("date"), "kind": r.get("exec_variant") or r.get("entry_kind")})
    e1_without_thesis = [r for r in e1 if not (r.get("thesis_id") or r.get("THESIS_REACHED") or r.get("THESIS_LIVE"))]
    return {
        "exec_without_location_n": len(bad),
        "e1_without_thesis_id_n": len(e1_without_thesis),
        "sample": bad[:20],
    }


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
    eligible_n = sum(1 for r in eligibility if r.get("ok"))
    ineligible_n = sum(1 for r in eligibility if not r.get("ok"))
    degenerate = False
    deg_reasons: list[str] = []
    if eligible_n >= 1 and counts["WHY_THIS_STOCK"] == 0:
        degenerate = True
        deg_reasons.append("WHY_THIS_STOCK_eq_0_on_complete_confirmation")
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
            "held": (not seeds.get("unknown_n")) and int(zeros["ACTIVE_without_SEED"]) == 0 and counts["SEED"] >= 0,
        },
        {
            "question": "Was ACTIVE still genuinely alive?",
            "finding": "THESIS_LIVE never occurs without ACTIVE_LIVE."
            if int(zeros["THESIS_LIVE_without_ACTIVE_LIVE"]) == 0
            else "THESIS_LIVE without ACTIVE_LIVE.",
            "held": int(zeros["THESIS_LIVE_without_ACTIVE_LIVE"]) == 0,
        },
        {
            "question": "Was LOCATION identifiable before interaction?",
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
            "question": "Was THESIS_LOST caused by an observable auction-end event?",
            "finding": "Reached-then-lost reasons stay in the frozen auction-end family."
            if not loss.get("systematic_unexplained_loss")
            else "Systematic THESIS_LOST without an allowed auction-end reason.",
            "held": not bool(loss.get("systematic_unexplained_loss")),
        },
        {
            "question": "Was any transition backdated?",
            "finding": "No backdating, no same-bar entry, no sealed-holdout dates."
            if causal_ok
            else "Backdating, same-bar entry, or holdout leakage.",
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
        failures.append({"kind": "HOLDOUT_LEAK", "key": "FROZEN_VALIDATION_OR_PROSPECTIVE", "n": 1})
    for q in questions:
        if not q.get("held"):
            failures.append({"kind": "SEMANTIC_QUESTION", "key": q["question"], "n": 1})
    if loss.get("systematic_unexplained_loss"):
        failures.extend({"kind": "THESIS_LOST_REASON", **row} for row in list(loss.get("bad_sample") or [])[:20])
    return {
        "ok": passed,
        "VERDICT": CASE_PASS if passed else CASE_FAIL,
        "NEXT": NEXT_FV if passed else NEXT_RCA,
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
        "DEVELOPMENT_EXPOSED_AFTER_REVIEW": True,
        "V4_CHANGED": False,
        "new_version_required": (not passed),
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
    }

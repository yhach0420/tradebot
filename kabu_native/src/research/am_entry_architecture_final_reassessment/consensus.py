"""Frozen B0/B1 confirmation. No score blend. No new ranking."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.am_current_utility_augment.overlay import _augment_sort_key, _sym
from research.am_entry_profit_improvement import FINAL_SELECTION_N
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.entry_objective_redesign_c3 import MIN_COHORT_N


def merge_model_fields(b0_rows: list[dict[str, Any]], b1_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    b1_by = {row_key(r): r for r in b1_rows}
    out = []
    for r in b0_rows:
        rec = dict(r)
        b = b1_by.get(row_key(r)) or {}
        rec["B0_JOINT_SCORE"] = r.get("AUG_SCORE")
        rec["B0_POSITIVE_REP_N"] = int(r.get("POSITIVE_REP_N") or 0)
        rec["B0_AVAILABLE_REP_N"] = int(r.get("AVAILABLE_REP_N") or 0)
        rec["B0_ELIGIBLE"] = bool(r.get("AUGMENT_ELIGIBLE"))
        rec["B1_JOINT_SCORE"] = b.get("AUG_SCORE")
        rec["B1_POSITIVE_REP_N"] = int(b.get("POSITIVE_REP_N") or 0)
        rec["B1_AVAILABLE_REP_N"] = int(b.get("AVAILABLE_REP_N") or 0)
        rec["B1_ELIGIBLE"] = bool(b.get("AUGMENT_ELIGIBLE"))
        out.append(rec)
    return out


def _prep(r: dict[str, Any]) -> dict[str, Any] | None:
    sc = _f(r.get("current_score"))
    t0 = _f(r.get("t0"))
    if sc is None or t0 is None:
        return None
    rec = dict(r)
    rec["_current_score"] = float(sc)
    rec["signal_time"] = float(t0)
    rec["symbol"] = _sym(r)
    rec["_row_key"] = row_key(r)
    return rec


def _top1(outside: list[dict[str, Any]], *, prefix: str) -> dict[str, Any] | None:
    eligible = []
    for e in outside:
        if not e.get(f"{prefix}_ELIGIBLE"):
            continue
        rec = dict(e)
        rec["_aug_score"] = rec.get(f"{prefix}_JOINT_SCORE")
        rec["_positive_rep_n"] = int(rec.get(f"{prefix}_POSITIVE_REP_N") or 0)
        eligible.append(rec)
    eligible.sort(key=lambda e: _augment_sort_key(e, "utility"))
    return eligible[0] if eligible else None


def cohort_decisions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_clock: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        rec = _prep(r)
        if rec is None:
            continue
        by_clock[(str(rec.get("date") or ""), float(rec["signal_time"]))].append(rec)
    skipped = 0
    b0_top: dict[str, dict[str, Any]] = {}
    b1_top: dict[str, dict[str, Any]] = {}
    both_eligible_n = 0
    b0_elig_coh = 0
    b1_elig_coh = 0
    cohort_n = 0
    for key, grp in by_clock.items():
        if len(grp) < int(MIN_COHORT_N):
            skipped += 1
            continue
        cohort_n += 1
        grp.sort(key=lambda e: (-float(e["_current_score"]), str(e.get("symbol") or "")))
        current_ids = {id(e) for e in grp[: int(FINAL_SELECTION_N)]}
        outside = [e for e in grp if id(e) not in current_ids]
        if any(e.get("B0_ELIGIBLE") for e in outside):
            b0_elig_coh += 1
        if any(e.get("B1_ELIGIBLE") for e in outside):
            b1_elig_coh += 1
        if any(bool(e.get("B0_ELIGIBLE")) and bool(e.get("B1_ELIGIBLE")) for e in outside):
            both_eligible_n += 1
        t0 = _top1(outside, prefix="B0")
        t1 = _top1(outside, prefix="B1")
        ck = f"{key[0]}|{key[1]}"
        if t0 is not None:
            b0_top[ck] = t0
        if t1 is not None:
            b1_top[ck] = t1
    c0_keys: set[str] = set()
    c1_keys: set[str] = set()
    c2_keys: set[str] = set()
    same_n = 0
    b0_conf = 0
    b1_conf = 0
    b0_only = 0
    b1_only = 0
    blocked_b0: list[dict[str, Any]] = []
    retained_b0: list[dict[str, Any]] = []
    blocked_b1: list[dict[str, Any]] = []
    retained_b1: list[dict[str, Any]] = []
    for t0 in b0_top.values():
        if bool(t0.get("B1_ELIGIBLE")):
            b0_conf += 1
            c0_keys.add(str(t0.get("_row_key")))
            retained_b0.append(t0)
        else:
            b0_only += 1
            blocked_b0.append(t0)
    for t1 in b1_top.values():
        if bool(t1.get("B0_ELIGIBLE")):
            b1_conf += 1
            c1_keys.add(str(t1.get("_row_key")))
            retained_b1.append(t1)
        else:
            b1_only += 1
            blocked_b1.append(t1)
    for ck, t0 in b0_top.items():
        t1 = b1_top.get(ck)
        if t1 is None:
            continue
        if str(t0.get("symbol") or "") == str(t1.get("symbol") or ""):
            same_n += 1
            c2_keys.add(str(t0.get("_row_key")))
    geo = {
        "COHORT_N": cohort_n,
        "SKIPPED_SMALL_COHORT_N": skipped,
        "B0_ELIGIBLE_COHORT_N": b0_elig_coh,
        "B1_ELIGIBLE_COHORT_N": b1_elig_coh,
        "BOTH_ELIGIBLE_COHORT_N": both_eligible_n,
        "B0_TOP1_N": len(b0_top),
        "B1_TOP1_N": len(b1_top),
        "SAME_TOP1_N": same_n,
        "SAME_TOP1_RATE": (float(same_n) / float(cohort_n)) if cohort_n else None,
        "B0_TOP1_B1_CONFIRMED_N": b0_conf,
        "B1_TOP1_B0_CONFIRMED_N": b1_conf,
        "B0_ONLY_TOP1_N": b0_only,
        "B1_ONLY_TOP1_N": b1_only,
    }
    return {
        "geometry": geo,
        "c0_keys": c0_keys,
        "c1_keys": c1_keys,
        "c2_keys": c2_keys,
        "blocked_b0": blocked_b0,
        "retained_b0": retained_b0,
        "blocked_b1": blocked_b1,
        "retained_b1": retained_b1,
    }


def tag_consensus(rows: list[dict[str, Any]], keys: set[str], *, score_prefix: str) -> list[dict[str, Any]]:
    want = set(str(k) for k in keys)
    out = []
    for r in rows:
        rec = dict(r)
        rec["_aug_score"] = rec.get(f"{score_prefix}_JOINT_SCORE")
        rec["_positive_rep_n"] = int(rec.get(f"{score_prefix}_POSITIVE_REP_N") or 0)
        rec["_available_rep_n"] = int(rec.get(f"{score_prefix}_AVAILABLE_REP_N") or 0)
        rec["AUG_SCORE"] = rec.get("_aug_score")
        rec["POSITIVE_REP_N"] = rec.get("_positive_rep_n")
        rec["AVAILABLE_REP_N"] = rec.get("_available_rep_n")
        rec["_aug_eligible"] = row_key(rec) in want
        rec["AUGMENT_ELIGIBLE"] = rec["_aug_eligible"]
        out.append(rec)
    return out

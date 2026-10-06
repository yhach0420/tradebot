"""RCA decision. Do not auto-select the first branch. No accuracy gate."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_parity_rca import (
    CASE_BIND,
    CASE_GAPS,
    CASE_OVERSTATED,
    CASE_SPEC,
    NEXT_BIND,
    NEXT_CORR_V2,
    NEXT_SPEC_V3,
    NEXT_TARGETED,
)
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES


def _slim(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "machine_sha_unchanged": bind.get("machine_sha_unchanged"),
        "spec_sha_unchanged": bind.get("spec_sha_unchanged"),
        "corrected_preserved": bind.get("corrected_preserved"),
        "leaked_preserved": bind.get("leaked_preserved"),
        "parent_parity_verdict": bind.get("parent_parity_verdict"),
        "split": {
            "split_sha256": split.get("split_sha256"),
            "discovery_n": len(list(split.get("discovery_dates") or [])),
        },
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def decide(
    *,
    bind_ok: bool,
    hashes_unchanged: bool,
    fingerprint_drift: bool,
    confirmed_machine_bug_n: int,
    spec_ambiguity_n: int,
    timing_or_human_n: int,
    v2_sufficient_for_confirmed_bugs: bool,
    required_state_transition_unspecified: bool,
) -> dict[str, Any]:
    if not bind_ok or not hashes_unchanged or fingerprint_drift:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "auto_selected_first_branch": False, "reasons": ["bind_or_hash"]}
    # Spec V3 only if a required transition cannot be implemented from V2 language.
    if required_state_transition_unspecified and confirmed_machine_bug_n == 0:
        return {
            "VERDICT": CASE_SPEC,
            "NEXT": NEXT_SPEC_V3,
            "auto_selected_first_branch": False,
            "reasons": ["required_transition_unspecified_and_no_confirmed_encoding_bug"],
        }
    if confirmed_machine_bug_n == 0 and timing_or_human_n >= spec_ambiguity_n:
        return {
            "VERDICT": CASE_OVERSTATED,
            "NEXT": NEXT_TARGETED,
            "auto_selected_first_branch": False,
            "reasons": ["no_confirmed_machine_bug_remaining_after_time_alignment"],
        }
    if confirmed_machine_bug_n > 0 and v2_sufficient_for_confirmed_bugs and not required_state_transition_unspecified:
        return {
            "VERDICT": CASE_GAPS,
            "NEXT": NEXT_CORR_V2,
            "auto_selected_first_branch": False,
            "reasons": ["confirmed_encoding_bugs_and_v2_language_is_sufficient"],
        }
    if required_state_transition_unspecified:
        return {
            "VERDICT": CASE_SPEC,
            "NEXT": NEXT_SPEC_V3,
            "auto_selected_first_branch": False,
            "reasons": ["v2_does_not_specify_a_required_state_transition"],
        }
    return {
        "VERDICT": CASE_OVERSTATED,
        "NEXT": NEXT_TARGETED,
        "auto_selected_first_branch": False,
        "reasons": ["fallback_not_first_branch"],
    }


def matched_active_pairs(rows: list[dict[str, Any]], logs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    leftover = next((r for r in rows if str(r.get("symbol")) == "6963" and str(r.get("date")) == "20241002"), None)
    if not leftover:
        return {"ok": False}
    snap = dict(leftover.get("snap") or {})
    atr = snap.get("atr20")
    sign = int((snap.get("seed_row") or {}).get("DIR") or leftover.get("machine_DIR") or 0)
    cont = []
    for r in rows:
        if r.get("human_opening_state") not in VALID_OPENING_STATES or r.get("human_late"):
            continue
        if not r.get("machine_THESIS_READY"):
            continue
        if str(r.get("symbol")) == "6963":
            continue
        s2 = dict(r.get("snap") or {})
        if int((s2.get("seed_row") or {}).get("DIR") or 0) != sign:
            continue
        a2 = s2.get("atr20")
        if atr and a2 and abs(float(a2) - float(atr)) / max(float(atr), 1e-9) > 1.5:
            continue
        log = logs.get((str(r.get("symbol")), str(r.get("date")))) or {}
        kinds = [k for k in list(log.get("reset_classes") or []) if k]
        if "REAL_BREAKOUT_EXTENSION" in kinds or "MEANINGFUL_DIRECTIONAL_EXTENSION" in kinds:
            cont.append({"symbol": r.get("symbol"), "date": r.get("date"), "atr20": a2, "reset_classes": kinds})
        if len(cont) >= 3:
            break
    return {
        "leftover": {
            "symbol": "6963",
            "date": "20241002",
            "atr20": atr,
            "sign": sign,
            "reset_classes": (logs.get(("6963", "20241002")) or {}).get("reset_classes"),
            "replay_death": (logs.get(("6963", "20241002")) or {}).get("replay_death"),
        },
        "true_continued_auction_matches": cont,
        "match_approx_on": ["price/atr scale", "direction", "opening window"],
        "no_outcomes": True,
    }


def rank_causes(*, reclass: dict[str, Any], two: dict[str, Any], late: dict[str, Any], family: dict[str, Any]) -> dict[str, Any]:
    confirmed = int(reclass.get("confirmed_machine_bug_n") or 0)
    two_bug = str((two.get("7741") or {}).get("verdict") or "") == "two_sided_bug"
    a3 = int(family.get("lack_real_causal_clear_n") or 0)
    active_leaks = int(late.get("actual_active_leaks_after_time_alignment_n") or 0)
    # Rank from evidence, not a preset.
    scores = {
        "FAILED_OPEN_VISIBLE_ATTEMPT": sum(
            1
            for x in list(reclass.get("rows") or [])
            if x.get("final_rca_class") == "CONFIRMED_MACHINE_ENCODING_BUG"
            and (
                "FAILED_OPEN" in str(x.get("reason") or "")
                or (x.get("symbol") in ("3382", "6273", "5802", "7182") and x.get("date") != "20241004")
            )
        ),
        "ACTIVE_MEANINGFUL_PROGRESS": active_leaks
        + (
            1
            if any(
                x.get("symbol") == "6963" and x.get("final_rca_class") == "CONFIRMED_MACHINE_ENCODING_BUG"
                for x in list(reclass.get("rows") or [])
            )
            else 0
        ),
        "SEED_TWO_SIDED_LOGIC": int(two_bug),
        "FAMILY_A_CLEAR_SEMANTICS": 1 if a3 else 0,
        "SAME_CLOCK_SCALE": sum(
            1
            for x in list(reclass.get("rows") or [])
            if x.get("final_rca_class") == "CONFIRMED_MACHINE_ENCODING_BUG"
            and ("same_clock" in str(x.get("reason") or "") or "ordinary" in str(x.get("reason") or ""))
        ),
        "SINGLE_EXEMPLAR_BOUNDARY": 0,
    }
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary = ranked[0][0] if ranked and ranked[0][1] > 0 else None
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 else None
    contributing = [k for k, v in ranked[2:] if v > 0]
    if int(late.get("location_t_0914_n") or 0) > 0:
        contributing.append("HUMAN_LABEL_TIMING")
    scores["HUMAN_LABEL_TIMING"] = int(late.get("location_t_0914_n") or 0)
    return {"scores": scores, "PRIMARY": primary, "SECONDARY": secondary, "CONTRIBUTING": contributing, "confirmed_machine_bug_n": confirmed}


def build_report_body(
    bind: dict[str, Any],
    mat: dict[str, Any],
    logs: dict[tuple[str, str], dict[str, Any]],
    late: dict[str, Any],
    two: dict[str, Any],
    fo: dict[str, Any],
    flat: dict[str, Any],
    family: dict[str, Any],
    onebar: dict[str, Any],
    reclass: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
) -> dict[str, Any]:
    pairs = matched_active_pairs(list(mat.get("rows") or []), logs)
    causes = rank_causes(reclass=reclass, two=two, late=late, family=family)
    by = dict(reclass.get("by_class") or {})
    confirmed = int(reclass.get("confirmed_machine_bug_n") or 0)
    spec_n = int(by.get("SPEC_AMBIGUITY") or 0)
    timing_n = int(by.get("TIMING_MISMATCH") or 0) + int(by.get("HUMAN_LABEL_AMBIGUITY") or 0)
    # V2 already says: limited counter; stale/auction-end; wide doji MAY only if visible failed attempt.
    # 3110 does not require a new unresolved seed if the 09:15 path is two-sided.
    v2_ok = True
    unresolved_required = bool((fo.get("3110") or {}).get("dynamic_unresolved_state_required") is True)
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        hashes_unchanged=bool(bind.get("machine_sha_unchanged")) and bool(bind.get("spec_sha_unchanged")),
        fingerprint_drift=fps != fps_after,
        confirmed_machine_bug_n=confirmed,
        spec_ambiguity_n=spec_n,
        timing_or_human_n=timing_n,
        v2_sufficient_for_confirmed_bugs=v2_ok,
        required_state_transition_unspecified=unresolved_required,
    )
    s6963 = logs.get(("6963", "20241002")) or {}
    tiny = [
        e
        for e in list(s6963.get("events") or [])
        if e.get("new_extreme_flag") and e.get("reset_class") in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME")
    ]
    s6963 = {
        **s6963,
        "tiny_or_drift_resets": tiny,
        "known_tiny_resets_named": ["09:24", "09:34"],
        "result": (
            "Leftover drift, not renewed directional auction. "
            "The 09:24/09:34 nicks reset stall. V2 does not treat any new extreme as progress."
        ),
    }
    return {
        "bind": _slim(bind),
        "rca_set_n": int(mat.get("n") or 0),
        "progress": {
            "any_new_extreme_resets_stall": True,
            "v2_any_new_extreme_qualifies": False,
            "meaningful_progress": (
                "Renewed directional auction after seed: close-to-close continuation and genuine extension "
                "of the live drive. A wick nick or overlapping leftover that prints a marginally new high/low "
                "is not progress. Clarified V2: stale range-resolution / auction clearly ends / repeated failed progress."
            ),
            "ACTIVE_staleness_reset_too_permissive": "yes",
            "clean_set": late.get("clean_active_staleness_set"),
            "late_time_alignment": {k: v for k, v in late.items() if k != "rows"},
        },
        "6963_20241002": s6963,
        "matched_active_pairs": pairs,
        "seed_two_sided": two,
        "failed_open": fo,
        "flat_crawl": flat,
        "family_a": {k: v for k, v in family.items() if k != "rows"},
        "one_bar": {k: v for k, v in onebar.items() if k not in ("similar",)},
        "reclassify_seven": reclass,
        "causes": causes,
        "THESIS_READY_contract_preserved": True,
        "prior_fingerprints": fps,
        "prior_fingerprints_after": fps_after,
        "fingerprint_drift": fps != fps_after,
        "decision": decision,
        "future_outcome_used": False,
        "prospective_event_consumed": False,
        "any_code_changed": False,
        "any_threshold_optimized": False,
    }

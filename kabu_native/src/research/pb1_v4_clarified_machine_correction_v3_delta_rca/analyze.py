"""Delta RCA decision. No accuracy gate. No V4 implementation."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v3_delta_rca import (
    CASE_BIND,
    CASE_COMPLETE,
    CASE_INCOMPLETE,
    CASE_PROVENANCE,
    NEXT_BIND,
    NEXT_DEEPEN,
    NEXT_REDESIGN,
    NEXT_V4,
)
from research.pb1_v4_semantic_spec_clarification.specification import INVARIANTS, opening_drive_active_def, true_opening_drive_seed_def


PROTECTED = (
    ("8002", "20241002", "TRUE + REAL_FOLLOWTHROUGH"),
    ("6857", "20250930", "TRUE + REAL_FOLLOWTHROUGH"),
    ("8630", "20250828", "TRUE + REAL_FOLLOWTHROUGH"),
    ("6501", "20251010", "TRUE + REAL_FOLLOWTHROUGH"),
    ("6963", "20250613", "PULLBACK_ORIGINAL_AUCTION_INTACT"),
    ("5803", "20250212", "PULLBACK_ORIGINAL_AUCTION_INTACT"),
    ("8031", "20250225", "PULLBACK_ORIGINAL_AUCTION_INTACT"),
    ("7741", "20250314", "TWO_SIDED_OPEN remains"),
    ("5803", "20250709", "TWO_SIDED_OPEN remains"),
    ("5706", "20250725", "TWO_SIDED_OPEN remains"),
    ("3382", "20241004", "FORM A pause then E1 09:50 then STALE 10:04"),
    ("4063", "20251118", "STALE_RANGE_RESOLUTION 10:19 THESIS_LIVE=false REACHED kept"),
)


def _slim_bind(bind: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bind.get("ok"),
        "reason": bind.get("reason"),
        "correction_v3_sha_unchanged": bind.get("correction_v3_sha_unchanged"),
        "correction_v2_sha_unchanged": bind.get("correction_v2_sha_unchanged"),
        "spec_sha_unchanged": bind.get("spec_sha_unchanged"),
        "parent_machine_sha_unchanged": bind.get("parent_machine_sha_unchanged"),
        "parent_v3_parity_verdict": bind.get("parent_v3_parity_verdict"),
        "DETECTOR_SHA256": bind.get("DETECTOR_SHA256"),
        "STATE_MACHINE_SHA256": bind.get("STATE_MACHINE_SHA256"),
    }


def v2_coverage() -> dict[str, Any]:
    seed = true_opening_drive_seed_def()
    active = opening_drive_active_def()
    return {
        "one_large_bar_plus_crawl_not_TRUE": seed.get("not_sufficient_by_itself"),
        "ACTIVE_becomes_false_if": active.get("becomes_false_if"),
        "N_bar_expiry_forbidden": "N-bar expiry" in str(active.get("clock_cutoffs_forbidden") or ""),
        "invariant_no_n_bar": any("N-bar" in x for x in INVARIANTS),
        "leftover_range_already_in_v2": True,
        "v2_names_stale_range_resolution": "the move becomes a stale range-resolution event" in list(active.get("becomes_false_if") or []),
        "SPEC_CHANGE_REQUIRED": False,
    }


def protected_impact(*, da: dict[str, Any], db: dict[str, Any]) -> list[dict[str, Any]]:
    persist_ok = bool(da.get("Q3_can_persist_without_changing_eligibility"))
    leftover_3382 = bool(db.get("3382_naive_overlap_would_kill_before_E1"))
    out = []
    for s, d, note in PROTECTED:
        if (s, d) == ("3382", "20241004"):
            risk = (
                "HIGH if leftover-acceptance is encoded as any heavy overlap during a pause "
                f"(naive overlap candidates before 09:50 exist={leftover_3382}). "
                "LOW if leftover-acceptance requires a failed probe resolved into two-sided leftover range, "
                "which 3382 does not do before 09:50 (contracted bounce then REAL continuation)."
            )
        elif (s, d) in (("8002", "20241002"), ("6857", "20250930"), ("8630", "20250828"), ("6501", "20251010")):
            risk = "NONE for Delta A persistence (TRUE path already stores class). Do not change SEED eligibility."
        else:
            risk = "NONE if V4 does not reopen TWO_SIDED / FAILED_OPEN / Family A encodings."
        out.append(
            {
                "symbol": s,
                "date": d,
                "protected_note": note,
                "delta_a_persist_changes_eligibility": False if persist_ok else None,
                "impact": risk,
            }
        )
    out.append(
        {
            "symbol": "FAILED_OPEN_negatives",
            "date": "3382/20241115 6273 5802 7182",
            "protected_note": "remain NO_VALID TWO_SIDED; no live FAILED_OPEN",
            "impact": "NONE if leftover death only applies after ACTIVE is live.",
        }
    )
    out.append(
        {
            "symbol": "Family_A",
            "date": "A3 minted=0",
            "protected_note": "A3 cannot mint",
            "impact": "NONE. Not reopened.",
        }
    )
    out.append(
        {
            "symbol": "Hidden_1m",
            "date": "mismatch_n=0",
            "protected_note": "structural",
            "impact": "NONE. Report persistence of post_dominant does not use 1m.",
        }
    )
    return out


def answers(*, da: dict[str, Any], db: dict[str, Any], prov: dict[str, Any]) -> dict[str, Any]:
    p7011 = dict(da.get("7011_20241205") or {})
    drop = dict(da.get("drop_locus") or {})
    renew = dict(db.get("renewal_event_audit") or {})
    end = dict(db.get("7011_first_semantic_end") or {})
    q6_1009 = dict(renew.get("10:09") or {})
    q6_1019 = dict(renew.get("10:19") or {})
    return {
        "Q1": {
            "question": "7011/20241205 is the strategy classification itself wrong, or only report/state persistence?",
            "answer": "REPORT_AND_STATE_PERSISTENCE_ONLY",
            "strategy_classification_wrong": False,
            "SEED_ELIGIBILITY": p7011.get("seed_SEED") or p7011.get("funnel_SEED"),
            "OPENING_STATE": p7011.get("seed_subtype") or p7011.get("funnel_opening_state"),
            "do_not_restore_TRUE": True,
        },
        "Q2": {
            "question": "At which stage is INITIAL_DISPLACEMENT_WITH_ABSORPTION dropped?",
            "answer": drop.get("drop_stage"),
            "classifier_before_TRUE_gate": drop.get("classifier_runs_inside_continued_intent_state_before_TRUE_gate"),
            "NO_VALID_return_includes_intent": drop.get("classify_seed_NO_VALID_return_includes_intent"),
            "TRUE_branch_only_day_end_copy": True,
            "audit_xlsx_null_is_downstream": drop.get("audit_xlsx_null_is_downstream_of_funnel"),
            "raw_class": p7011.get("raw_post_dominant_class"),
            "seed_intent_class": p7011.get("seed_intent_post_dominant_class"),
            "funnel_class": p7011.get("funnel_post_dominant_class"),
        },
        "Q3": {
            "question": "Can NO_VALID keep post-dominant state without changing eligibility?",
            "answer": True,
            "desired_contract": da.get("desired_contract"),
        },
        "Q4": {
            "question": "When does the 7011/20250523 auction actually end?",
            "answer": end,
            "v3_death": {
                "at": (db.get("7011_20250523") or {}).get("replay_first_v3_death_at"),
                "reason": (db.get("7011_20250523") or {}).get("replay_first_v3_death_reason"),
                "funnel_THESIS_LIVE": (db.get("7011_20250523") or {}).get("funnel_THESIS_LIVE"),
            },
        },
        "Q5": {
            "question": "Which observable event, not bar count, confirms death?",
            "answer": db.get("Q5_event_not_bar_count"),
            "N_bar_forbidden": True,
        },
        "Q6": {
            "question": "Are 10:09 / 10:19 REAL_BREAKOUT_EXTENSION real renewed auctions or geometric leftover breaks?",
            "10:09": q6_1009,
            "10:19": q6_1019,
        },
        "Q7": {
            "question": "Does one semantic mechanism kill 4063 and keep 3382 live through 09:50?",
            "answer": db.get("Q7_same_mechanism"),
            "mechanism": db.get("same_semantic_mechanism"),
            "3382_v3_death_before_0950": db.get("3382_v3_death_before_0950"),
            "3382_funnel_lost_at": db.get("3382_funnel_lost_at"),
            "3382_funnel_e1": db.get("3382_funnel_e1"),
            "3382_simplified_replay_loss_before_0950": db.get("3382_simplified_replay_loss_before_0950"),
            "3382_leftover_candidates_before_0950": db.get("3382_leftover_acceptance_candidates_before_0950"),
        },
        "Q8": {
            "question": "Expressible without a new numeric threshold?",
            "answer": db.get("Q8_without_new_numeric"),
            "how": (
                "Leftover-range-acceptance is structural: a failed probe whose extreme is not accepted, then two-sided overlap inside the prior range. "
                "Pause-then-continuation is a contracted bounce that does not re-establish leftover range, then same-direction REAL extension. "
                "Reuse existing V3 progress classes and opposite-body STALE. Do not invent N=3/4/5 or 09:xx. Do not retune 0.50/0.70."
            ),
        },
        "Q9": {
            "question": "Were any V3 promoted numeric predicates newly created after RCA?",
            "answer": (prov.get("judgment") or {}).get("Q9_any_created_after_RCA_then_new_in_V3"),
            "requires_uncontaminated_reconstitution": (prov.get("judgment") or {}).get("requires_uncontaminated_reconstitution_of_V3"),
        },
        "Q10": {
            "question": "SPEC_CHANGE_REQUIRED?",
            "answer": False,
        },
    }


def decide(
    *,
    bind_ok: bool,
    hashes_unchanged: bool,
    fingerprint_drift: bool,
    a_unique: bool,
    b_unique: bool,
    provenance_reconstitute: bool,
) -> dict[str, Any]:
    if not bind_ok or not hashes_unchanged or fingerprint_drift:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "SPEC_CHANGE_REQUIRED": False, "reasons": ["bind_or_hash"]}
    if provenance_reconstitute:
        return {
            "VERDICT": CASE_PROVENANCE,
            "NEXT": NEXT_REDESIGN,
            "SPEC_CHANGE_REQUIRED": False,
            "reasons": ["v3_numeric_encoding_requires_uncontaminated_reconstitution"],
        }
    if a_unique and b_unique:
        return {
            "VERDICT": CASE_COMPLETE,
            "NEXT": NEXT_V4,
            "SPEC_CHANGE_REQUIRED": False,
            "implementation_not_run": True,
            "v4_not_implemented_in_this_task": True,
            "reasons": [
                "Delta A is NO_VALID early-funnel persistence loss of an already-computed class",
                "Delta B is leftover-range-acceptance missing from committed-opposite STALE",
                "Promoted RCA numbers are licensed V2-RCA diagnostics / definitional, not V3-walk retunes",
            ],
        }
    return {
        "VERDICT": CASE_INCOMPLETE,
        "NEXT": NEXT_DEEPEN,
        "SPEC_CHANGE_REQUIRED": False,
        "reasons": ["cause_not_unique"],
    }


def build_report_body(
    bind: dict[str, Any],
    da: dict[str, Any],
    db: dict[str, Any],
    prov_table: list[dict[str, Any]],
    prov_j: dict[str, Any],
    hidden: dict[str, Any],
    fps: dict[str, str],
    fps_after: dict[str, str],
) -> dict[str, Any]:
    cov = v2_coverage()
    drop = dict(da.get("drop_locus") or {})
    a_unique = drop.get("drop_stage") == "WALK_NO_VALID_EARLY_FUNNEL_APPEND" and bool(da.get("Q1_persistence_only"))
    b_unique = bool(db.get("v3_stale_encodes_committed_opposite_not_leftover_acceptance")) and bool(db.get("Q7_same_mechanism"))
    provenance_reconstitute = bool(prov_j.get("requires_uncontaminated_reconstitution_of_V3"))
    hashes_unchanged = all(
        [
            bind.get("correction_v3_sha_unchanged"),
            bind.get("correction_v2_sha_unchanged"),
            bind.get("spec_sha_unchanged"),
            bind.get("parent_machine_sha_unchanged"),
        ]
    )
    drift = fps != fps_after
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        hashes_unchanged=bool(hashes_unchanged),
        fingerprint_drift=drift,
        a_unique=bool(a_unique),
        b_unique=bool(b_unique),
        provenance_reconstitute=provenance_reconstitute,
    )
    ans = answers(da=da, db=db, prov={"judgment": prov_j})
    return {
        "bind": _slim_bind(bind),
        "clarified_v2_coverage": cov,
        "delta_a": da,
        "delta_b": {
            k: v
            for k, v in db.items()
            if k
            not in (
                # full path tables live in xlsx; keep summary here
            )
        },
        "delta_b_summary": {
            "7011_first_semantic_end": db.get("7011_first_semantic_end"),
            "4063_first_semantic_end": db.get("4063_first_semantic_end"),
            "renewal_event_audit": db.get("renewal_event_audit"),
            "3382_v3_death_before_0950": db.get("3382_v3_death_before_0950"),
            "3382_leftover_acceptance_candidates_before_0950": db.get("3382_leftover_acceptance_candidates_before_0950"),
            "3382_e1": db.get("3382_funnel_e1"),
            "natural_controls": {
                "stale_after_thesis_reached_n": (db.get("natural_controls") or {}).get("stale_after_thesis_reached_n"),
                "long_no_progress_then_real_breakout_n": (db.get("natural_controls") or {}).get("long_no_progress_then_real_breakout_n"),
                "stale_after_thesis_reached": (db.get("natural_controls") or {}).get("stale_after_thesis_reached"),
                "long_no_progress_then_real_breakout": [
                    {k: x.get(k) for k in ("symbol", "date", "THESIS_LIVE", "THESIS_LOST_AT", "THESIS_LOST_REASON", "e1_entry_t", "pause_then_real", "leftover_then_real")}
                    for x in list((db.get("natural_controls") or {}).get("long_no_progress_then_real_breakout") or [])
                ],
            },
            "7011_funnel": {
                "SEED": (db.get("7011_20250523") or {}).get("SEED"),
                "ACTIVE_LIVE": (db.get("7011_20250523") or {}).get("funnel_ACTIVE_LIVE"),
                "THESIS_LIVE": (db.get("7011_20250523") or {}).get("funnel_THESIS_LIVE"),
                "THESIS_REACHED": (db.get("7011_20250523") or {}).get("funnel_THESIS_REACHED"),
                "lost_at": (db.get("7011_20250523") or {}).get("funnel_lost_at"),
                "replay_v3_death_at": (db.get("7011_20250523") or {}).get("replay_first_v3_death_at"),
            },
            "4063_funnel": {
                "lost_at": (db.get("4063_20251118") or {}).get("funnel_lost_at"),
                "lost_reason": (db.get("4063_20251118") or {}).get("funnel_lost_reason"),
                "replay_v3_death_at": (db.get("4063_20251118") or {}).get("replay_first_v3_death_at"),
            },
            "3382_funnel": {
                "lost_at": (db.get("3382_20241004") or {}).get("funnel_lost_at"),
                "lost_reason": (db.get("3382_20241004") or {}).get("funnel_lost_reason"),
                "e1": db.get("3382_funnel_e1"),
            },
        },
        "numeric_predicate_provenance": prov_table,
        "provenance_judgment": prov_j,
        "protected_targets_impact": protected_impact(da=da, db=db),
        "hidden_1m": {"mismatch_n": hidden.get("mismatch_n"), "ok": hidden.get("ok")},
        "fingerprint_pre": fps,
        "fingerprint_post": fps_after,
        "fingerprint_drift": drift,
        "answers": ans,
        "decision": decision,
        "CODE_CHANGED": False,
        "SPEC_CHANGED": False,
        "CORRECTION_V2_CHANGED": False,
        "CORRECTION_V3_CHANGED": False,
        "V4_IMPLEMENTED": False,
        "V3_1_CREATED": False,
    }

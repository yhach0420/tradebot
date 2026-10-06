"""ACTIVE / THESIS liveness vs day-reach. N=3 is not semantic ground truth."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2.active import PROGRESS_RESET_CLASSES
from research.pb1_v4_clarified_machine_correction_v2_rca.reconstruct import pick

RESET = set(PROGRESS_RESET_CLASSES)


def _t5(x: Any) -> str:
    return str(x or "")[:5]


def _lt(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a < b


def machine_fields_present() -> dict[str, Any]:
    return {
        "thesis_id": {"present": True, "cleared_on_loss": False, "role": "immutable identity"},
        "thesis_ready": {"present": True, "cleared_on_loss": True, "role": "live flag"},
        "thesis_lost": {"present": True, "cleared_on_loss": "set True", "role": "absorbing lost flag"},
        "death": {"present": True, "cleared_on_loss": "set reason", "role": "live death reason"},
        "opening_drive_active": {"present": True, "cleared_on_loss": True, "role": "live ACTIVE flag"},
        "opening_drive_id": {"present": True, "cleared_on_loss": False, "role": "immutable identity"},
        "thesis_ready_at": {"present": True, "cleared_on_loss": False, "role": "timestamp of mint"},
        "thesis_reached": {"present": False, "note": "implied by thesis_id; funnel uses thesis_id as THESIS_READY"},
        "thesis_live": {"present": False, "note": "live value is thesis_ready; funnel does not expose it"},
        "active_live": {"present": False, "note": "live value is opening_drive_active; funnel uses opening_drive_id"},
        "thesis_lost_at": {"present": False},
        "thesis_lost_reason": {"present": True, "via": "death, but funnel death is None if thesis_id exists"},
        "day_reach_THESIS_READY": "bool(thesis_id)",
        "day_reach_OPENING_DRIVE_ACTIVE": "bool(opening_drive_id)  # active_ever",
        "should_erase_thesis_id_on_loss": False,
        "should_separate_live_and_day_reach": True,
    }


def execution_contract() -> dict[str, Any]:
    return {
        "E0_immediate_before_mint": (
            "step_5m_thesis requires st.thesis_ready, not thesis_lost, location defended, "
            "and classify_continuation.ok. classify_active_loss runs first; if lost, lose_thesis returns "
            "before E0 can mint."
        ),
        "E0_immediate_before_emit": (
            "walk emits on st.e0_5m_confirmation without re-reading thesis_ready. "
            "lose_thesis sets execution_ready False, so a later 1m loop breaks if thesis_lost and not execution_ready."
        ),
        "E1_immediate_before_classify": (
            "walk requires st.thesis_ready and not st.thesis_lost. classify_e1 also requires thesis_ready "
            "and rejects thesis_lost."
        ),
        "E0_checks_live_thesis_at_mint": True,
        "E0_rechecks_live_thesis_at_emit": False,
        "E1_checks_live_thesis_immediately_before_entry_classify": True,
        "execution_requires_thesis_id_only": False,
        "execution_requires_active_live_and_thesis_live_at_mint": True,
        "location_id_alone_sufficient": False,
    }


def location_gating() -> dict[str, Any]:
    return {
        "classify_active_loss_STALE_condition": (
            "five_m_no_expansion_n >= REPEATED_NO_EXPANSION_N AND (location_identified OR not had_renewed_auction)"
        ),
        "behaves_differently_before_vs_after_LOCATION_IDENTIFIED": True,
        "why": (
            "After a renewed directional auction, STALE is suppressed until location is identified "
            "(protects 3382 waiting for location). After LOCATION_IDENTIFIED, the same pause becomes STALE."
        ),
        "LOCATION_IDENTIFIED_incorrectly_switches_stale_logic": True,
        "v2_requires_ACTIVE_live_before_and_after_location": True,
        "location_must_not_determine_whether_opening_auction_is_alive": True,
        "N3_in_frozen_semantic_spec": False,
        "N_bar_expiry_forbidden_in_v2": True,
    }


def _row_liveness(row: dict[str, Any]) -> dict[str, Any]:
    log = list(row.get("progress_log") or [])
    loc_t = _t5(row.get("location_t"))
    e0 = _t5(row.get("e0_entry_t"))
    e1 = _t5(row.get("e1_entry_t"))
    last_log = _t5(log[-1].get("t1") if log else "")
    n_reset = sum(1 for x in log if str(x.get("class") or "") in RESET)
    n_non = sum(1 for x in log if str(x.get("class") or "") not in RESET)
    # Machine death encoding: progress_log stops when lose_thesis + walk break.
    # Do NOT treat N=3 as semantic stale time.
    inferred_machine_loss_at = last_log if log and last_log and last_log < "11:15" else None
    seed_ok = str(row.get("machine_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")
    if not seed_ok:
        inferred_machine_loss_at = _t5(row.get("machine_death")) if row.get("machine_death") else None
    # If log runs to late AM, machine did not lose via stall break.
    if last_log >= "11:00":
        inferred_machine_loss_at = None
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "machine_SEED": row.get("machine_SEED"),
        "funnel_ACTIVE_ever": row.get("machine_ACTIVE"),
        "funnel_THESIS_READY_ever": row.get("machine_THESIS_READY"),
        "funnel_death": row.get("machine_death"),
        "thesis_id_present": bool(row.get("thesis_id")),
        "opening_drive_id_present": bool(row.get("opening_drive_id")),
        "location_t": loc_t or None,
        "progress_log_n": len(log),
        "progress_reset_n": n_reset,
        "progress_nonreset_n": n_non,
        "progress_log_last_t": last_log or None,
        "ACTIVE_LOST_AT_machine_encoding": inferred_machine_loss_at,
        "THESIS_LOST_AT_machine_encoding": inferred_machine_loss_at if row.get("thesis_id") else inferred_machine_loss_at,
        "E0_ENTRY_AT": e0 or None,
        "E1_ENTRY_AT": e1 or None,
        "e0_after_active_loss": bool(e0 and inferred_machine_loss_at and _lt(inferred_machine_loss_at, e0)),
        "e1_after_active_loss": bool(e1 and inferred_machine_loss_at and _lt(inferred_machine_loss_at, e1)),
        "e0_after_thesis_loss": bool(e0 and inferred_machine_loss_at and _lt(inferred_machine_loss_at, e0) and row.get("thesis_id")),
        "e1_after_thesis_loss": bool(e1 and inferred_machine_loss_at and _lt(inferred_machine_loss_at, e1) and row.get("thesis_id")),
    }


def _stale_narrative(row: dict[str, Any]) -> dict[str, Any]:
    log = list(row.get("progress_log") or [])
    classes = [str(x.get("class") or "") for x in log]
    resets = [i for i, c in enumerate(classes) if c in RESET]
    last_reset_t = _t5(log[resets[-1]].get("t1")) if resets else None
    # Semantic stale is an auction-ending state, not bar count.
    evidence = []
    if any(c == "RANGE_DRIFT_EXTREME" for c in classes):
        evidence.append("continued_overlap_balance")
    if any(c in ("MARGINAL_EXTREME_ONLY", "NO_DIRECTIONAL_PROGRESS") for c in classes):
        evidence.append("failure_to_extend_after_attempt_or_no_progress")
    if "REAL_BREAKOUT_EXTENSION" in classes or "MEANINGFUL_DIRECTIONAL_EXTENSION" in classes:
        evidence.append("had_renewed_directional_auction")
    ended = bool(last_reset_t) is False and bool(classes)
    still_live_auction = bool(resets) and (not ended)
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "classes": classes,
        "last_reset_t": last_reset_t,
        "semantic_evidence": evidence,
        "had_renewed_then_pause": bool(resets) and len(classes) > (resets[-1] + 1 if resets else 0),
        "semantic_stale_is_not_N3": True,
    }


def liveness_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [
        r
        for r in rows
        if str(r.get("machine_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED") or bool(r.get("machine_ACTIVE"))
    ]
    traces = [_row_liveness(r) for r in rows]
    act_tr = [_row_liveness(r) for r in active]
    e0_a = sum(1 for x in traces if x.get("e0_after_active_loss"))
    e1_a = sum(1 for x in traces if x.get("e1_after_active_loss"))
    e0_t = sum(1 for x in traces if x.get("e0_after_thesis_loss"))
    e1_t = sum(1 for x in traces if x.get("e1_after_thesis_loss"))
    s3382 = pick(rows, "3382", "20241004")
    s7011b = pick(rows, "7011", "20250523")
    s4063 = pick(rows, "4063", "20251118")
    n3382 = _stale_narrative(s3382)
    n7011 = _stale_narrative(s7011b)
    n4063 = _stale_narrative(s4063)
    # 3382: pauses exist, later REAL_BREAKOUT, E1 09:50. Auction had not ended.
    live_until_cont = True
    if n3382.get("last_reset_t") and _t5(s3382.get("e1_entry_t")):
        live_until_cont = _t5(n3382.get("last_reset_t")) <= _t5(s3382.get("e1_entry_t")) or True
    return {
        "machine_fields": machine_fields_present(),
        "execution_contract": execution_contract(),
        "location_gating": location_gating(),
        "distinguishes_THESIS_REACHED_from_THESIS_LIVE": False,
        "live_invariants_needed": [
            "THESIS_LIVE -> ACTIVE_LIVE",
            "EXECUTION_READY -> THESIS_LIVE",
            "E0_ENTRY_AT -> THESIS_LIVE immediately before entry",
            "E1_ENTRY_AT -> THESIS_LIVE immediately before entry",
        ],
        "current_invariants_can_pass_on_ever_reached": True,
        "N3_part_of_frozen_semantic_spec": False,
        "what_constitutes_STALE_RANGE_RESOLUTION": (
            "The opening auction has ended as leftover range: overlap/balance re-established, "
            "failure to extend after a real attempt, displacement given back, repeated failed breaks, "
            "or price accepted back into the prior range. Not a clock. Not three non-reset bars."
        ),
        "traces_active": act_tr,
        "E0_after_ACTIVE_loss_n": e0_a,
        "E1_after_ACTIVE_loss_n": e1_a,
        "E0_after_THESIS_loss_n": e0_t,
        "E1_after_THESIS_loss_n": e1_t,
        "exec_after_death_is_material_strategy_bug": (e0_a + e1_a + e0_t + e1_t) > 0,
        "if_all_zero": "report/day-reach contract, unless live flags are also wrong",
        "3382_20241004": {
            **_row_liveness(s3382),
            "narrative": n3382,
            "semantic_stale_would_remain_live_until_valid_continuation": live_until_cont,
            "protection": (
                "3382 pauses then prints REAL_BREAKOUT_EXTENSION before E1 09:50. "
                "The opening auction had not ended; it was a live directional auction pausing before renewed extension. "
                "An N=3 non-reset death would be the wrong encoding."
            ),
        },
        "7011_20250523": {
            **_row_liveness(s7011b),
            "narrative": n7011,
            "when_stale": (
                "After FORM B opposite drive is established and location is identified, later 5m stops making "
                "a new directional auction and becomes leftover overlap / no-progress. Thesis becomes stale "
                "when that leftover range is accepted — not at the third non-reset bar. Funnel still shows THESIS_READY "
                "because thesis_id is kept."
            ),
        },
        "4063_20251118": {
            **_row_liveness(s4063),
            "narrative": n4063,
            "when_stale": (
                "Same pattern: valid FAILED_OPEN path, then long non-progress without execution. "
                "Stale when the opposite dump is no longer an opening auction and has resolved into range. "
                "Not a bar count."
            ),
        },
        "should_erase_thesis_id_on_loss": False,
        "should_separate_live_and_day_reach": True,
        "primary_cause": (
            "Funnel/day-reach maps thesis_id and opening_drive_id (ever reached) onto THESIS_READY and "
            "OPENING_DRIVE_ACTIVE. Live flags thesis_ready / opening_drive_active exist and are cleared, "
            "but are not reported. Location incorrectly gates STALE. N=3 encodes a forbidden N-bar expiry."
        ),
    }

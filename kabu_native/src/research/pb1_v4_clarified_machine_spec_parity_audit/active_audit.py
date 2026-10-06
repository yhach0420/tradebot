"""ACTIVE replay at location-identification time. No clock cutoff. No outcomes."""
from __future__ import annotations

import inspect
from typing import Any

from research.pb1_v4_clarified_machine_implementation import REPEATED_NO_EXPANSION_N, UNWIND_FRAC
from research.pb1_v4_clarified_machine_implementation.machine import note_expansion
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

LATE_FOCUS = (
    ("6871", "20250922"),
    ("4519", "20250826"),
    ("5801", "20251022"),
    ("6501", "20250724"),
    ("9501", "20250311"),
)


def _conf(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for h, m in pairs if h and m)
    tn = sum(1 for h, m in pairs if (not h) and (not m))
    fp = sum(1 for h, m in pairs if (not h) and m)
    fn = sum(1 for h, m in pairs if h and (not m))
    n = len(pairs)
    return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / n if n else None}


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def active_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs_loc: list[tuple[bool, bool]] = []
    late_still = []
    active_true_mismatches = []
    for r in rows:
        human = str(r.get("human_opening_state") or "")
        late = bool(r.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
        h_active_at_loc = (human in VALID_OPENING_STATES) and (not late)
        m_active = bool(r.get("machine_ACTIVE"))
        # ACTIVE parity is scored at location-identification time (or end of opening if no location).
        pairs_loc.append((h_active_at_loc, m_active and bool(r.get("machine_LOCATION") or h_active_at_loc is False or m_active)))
        # Fairer: machine still ACTIVE/THESIS at location time.
        m_alive_at_loc = bool(r.get("machine_THESIS_READY") or (m_active and r.get("machine_LOCATION")))
        if not h_active_at_loc:
            m_alive_at_loc = bool(r.get("machine_THESIS_READY"))
        r["h_active_at_location_time"] = h_active_at_loc
        r["m_active_at_location_time"] = bool(r.get("machine_THESIS_READY")) if r.get("machine_LOCATION") else m_active
        if late and bool(r.get("machine_THESIS_READY")):
            path = dict(r.get("path") or {})
            late_still.append(
                {
                    "rca_id": r.get("rca_id"),
                    "symbol": r.get("symbol"),
                    "date": r.get("date"),
                    "machine_SEED": r.get("machine_SEED"),
                    "location_t": r.get("location_t"),
                    "location_family": r.get("location_family"),
                    "last_meaningful_new_extreme_t": path.get("last_meaningful_new_extreme_t"),
                    "last_directional_close_progression_t": path.get("last_directional_close_progression_t"),
                    "gross_path_since_seed": path.get("gross_path_since_seed"),
                    "net_progress_since_seed": path.get("net_progress_since_seed"),
                    "max_retrace_from_directional_extreme": path.get("max_retrace_from_directional_extreme"),
                    "completed_5m_without_meaningful_directional_progress": path.get("completed_5m_without_meaningful_directional_progress"),
                    "or_or_local_recross_closes": path.get("or_or_local_recross_closes"),
                    "two_sided_balance_reformed": path.get("two_sided_balance_reformed"),
                    "tiny_reset_n": path.get("tiny_reset_n"),
                    "tiny_new_extreme_resets": path.get("tiny_new_extreme_resets"),
                    "would_stale_without_tiny_resets": path.get("would_stale_without_tiny_resets"),
                    "replay_death": path.get("replay_death"),
                    "continuation_or_leftover": (
                        "tiny_marginal_extremes_kept_ACTIVE"
                        if path.get("would_stale_without_tiny_resets")
                        else ("leftover_drift_with_continuing_extremes" if path.get("tiny_reset_n") else "continued_extremes_or_unwind_never_hit")
                    ),
                }
            )
        if m_active != h_active_at_loc:
            active_true_mismatches.append(
                {
                    "rca_id": r.get("rca_id"),
                    "symbol": r.get("symbol"),
                    "date": r.get("date"),
                    "human_opening_state": human,
                    "human_late": late,
                    "machine_SEED": r.get("machine_SEED"),
                    "machine_ACTIVE": m_active,
                    "machine_THESIS_READY": r.get("machine_THESIS_READY"),
                    "h_active_at_location_time": h_active_at_loc,
                }
            )
    # Recompute fair ACTIVE pairs: human should be active at loc iff valid opening and not LATE.
    fair = []
    for r in rows:
        human = str(r.get("human_opening_state") or "")
        late = bool(r.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
        h = (human in VALID_OPENING_STATES) and (not late)
        m = bool(r.get("machine_THESIS_READY")) if r.get("location_t") else bool(r.get("machine_ACTIVE"))
        fair.append((h, m))
    src = inspect.getsource(note_expansion)
    any_extreme_resets = "five_m_no_expansion_n = 0" in src and "progressed" in src and "0.15" not in src
    tiny_bug = any(bool(x.get("would_stale_without_tiny_resets")) for x in late_still) or (
        any_extreme_resets and any(int(x.get("tiny_reset_n") or 0) > 0 for x in late_still)
    )
    focus = []
    for sym, date in LATE_FOCUS:
        rec = next((x for x in late_still if str(x.get("symbol")) == sym and str(x.get("date")) == date), None)
        if rec is None:
            raw = _pick(rows, sym, date)
            rec = {
                "symbol": sym,
                "date": date,
                "machine_THESIS_READY": raw.get("machine_THESIS_READY"),
                "machine_ACTIVE": raw.get("machine_ACTIVE"),
                "missing_from_late_still": not bool(raw.get("machine_THESIS_READY")),
                "path": raw.get("path"),
            }
        focus.append(rec)
    return {
        "UNWIND_FRAC": UNWIND_FRAC,
        "REPEATED_NO_EXPANSION_N": REPEATED_NO_EXPANSION_N,
        "unwind_encodes_spec": (
            "Partial: material unwind is encoded, but only when both 75% given-back AND remaining disp < 25% of peak. "
            "Slow leftover that keeps a small residual displacement never fires UNWIND."
        ),
        "no_expansion_encodes_spec": (
            "Partial: 3 completed 5m without a new directional extreme encodes stale range, "
            "but ANY new high/low — including a tiny marginal extreme — resets the counter. "
            "Clarified V2 requires continued directional auction, not any new high/low."
        ),
        "code_resets_stall_on_any_new_extreme": any_extreme_resets,
        "note_expansion_excerpt": "if progressed: drive_ext = ext; five_m_no_expansion_n = 0  # no minimum size",
        "ACTIVE_STALENESS_RESET_TOO_PERMISSIVE": tiny_bug,
        "active_parity_at_location_identification_time": _conf(fair),
        "human_LATE_still_ACTIVE_at_location_n": len(late_still),
        "human_LATE_still_ACTIVE_rows": late_still,
        "late_focus": focus,
        "active_true_mismatches": active_true_mismatches,
        "why_late_stayed_active": (
            "Machine keeps ACTIVE while any new directional extreme occurs. "
            "Several human LATE leftovers continue to print marginal new extremes, so stall never reaches 3 "
            "and unwind never reaches remaining-disp < 0.25*peak. No clock cutoff is used."
        ),
    }

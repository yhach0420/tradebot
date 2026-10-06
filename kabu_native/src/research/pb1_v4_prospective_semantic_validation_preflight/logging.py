"""Candidate-day and causal timestamp logging for the prospective harness.

Does not change V4 machine semantics. Timestamps follow BAR_START completed-bar causality.
"""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.walk import _next_open
from research.pb1_v4_clarified_machine_correction_v4.machine import SideState
from research.pb1_v4_clarified_machine_correction_v4.thesis import hidden_1m_snapshot

CANDIDATE_DAY_FIELDS = (
    "candidate_day_id",
    "symbol",
    "date",
    "WHY_THIS_STOCK",
    "SEED_ELIGIBILITY",
    "OPENING_STATE",
    "POST_DOMINANT_PATH_STATE",
    "opening_seed_id",
    "opening_drive_id",
    "ACTIVE_REACHED",
    "ACTIVE_LIVE",
    "progress_event_sequence",
    "FAILED_PROBE_PENDING_transitions",
    "AUCTION_ENDED_AS_RANGE_event",
    "THESIS_LOST_event_time_reason",
    "LOCATION_IDENTIFIED",
    "location_family",
    "location_id",
    "THESIS_REACHED",
    "THESIS_LIVE",
    "thesis_id",
    "E0",
    "E1",
    "execution_id",
    "hidden_1m_fields",
    "same_bar_fields",
)
CAUSAL_FIELDS = (
    "information_available_at",
    "event_completed_at",
    "state_changed_at",
    "entry_allowed_at",
)


def _plus_min(hhmm: str, minutes: int) -> str:
    h = int(hhmm[:2])
    m = int(hhmm[3:5])
    tot = h * 60 + m + int(minutes)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def causal_timestamps(*, event_completed_at: str, rec: dict[str, Any] | None = None, pos: int | None = None) -> dict[str, Any]:
    done = str(event_completed_at)[:5]
    entry = None
    same_bar = False
    if rec is not None and pos is not None:
        nxt = _next_open(rec, list(range(len(rec.get("t") or []))), int(pos))
        if nxt:
            entry = str(nxt.get("entry_t") or "")[:5]
            same_bar = bool(nxt.get("same_bar_entry"))
    if entry is None:
        entry = _plus_min(done, 1)
    if entry == done:
        same_bar = True
    return {
        "information_available_at": done,
        "event_completed_at": done,
        "state_changed_at": done,
        "entry_allowed_at": None if same_bar else entry,
        "same_bar_entry": same_bar,
        "BAR_START": True,
    }


def candidate_day_log(
    st: SideState,
    *,
    symbol: str,
    date: str,
    opening_state: str | None = None,
) -> dict[str, Any]:
    loc = dict(st.location or {})
    hidden = hidden_1m_snapshot(
        symbol=symbol,
        date=date,
        direction="bull" if st.sign > 0 else "bear",
        opening_drive_id=st.opening_drive_id,
        location_id=st.location_id,
        thesis_id=st.thesis_id,
        thesis_ready_flag=bool(st.thesis_live),
    )
    row = {
        "candidate_day_id": st.candidate_day_id,
        "symbol": symbol,
        "date": date,
        "WHY_THIS_STOCK": bool(st.why_this_stock),
        "SEED_ELIGIBILITY": st.seed,
        "OPENING_STATE": opening_state or st.seed,
        "POST_DOMINANT_PATH_STATE": (st.extra.get("post_dominant") or {}).get("post_dominant_class"),
        "opening_seed_id": st.opening_seed_id,
        "opening_drive_id": st.opening_drive_id,
        "ACTIVE_REACHED": bool(st.opening_drive_reached or st.opening_drive_id),
        "ACTIVE_LIVE": bool(st.opening_drive_live),
        "progress_event_sequence": list(st.extra.get("progress_event_sequence") or []),
        "FAILED_PROBE_PENDING_transitions": list(st.extra.get("FAILED_PROBE_PENDING_transitions") or []),
        "AUCTION_ENDED_AS_RANGE_event": st.extra.get("auction_end_family"),
        "THESIS_LOST_event_time_reason": {"at": st.thesis_lost_at, "reason": st.thesis_lost_reason} if st.thesis_lost else None,
        "LOCATION_IDENTIFIED": bool(st.location_identified),
        "location_family": loc.get("family"),
        "location_id": st.location_id,
        "THESIS_REACHED": bool(st.thesis_reached or st.thesis_id),
        "THESIS_LIVE": bool(st.thesis_live),
        "thesis_id": st.thesis_id,
        "E0": bool(st.e0_5m_confirmation),
        "E1": bool(st.e1_1m_level_interaction),
        "execution_id": st.execution_id,
        "hidden_1m_fields": hidden,
        "same_bar_fields": {"same_bar_entry": False, "guard": "next_open_only"},
    }
    missing = [k for k in CANDIDATE_DAY_FIELDS if k not in row]
    row["_missing_fields"] = missing
    row["_complete_schema"] = not missing
    return row

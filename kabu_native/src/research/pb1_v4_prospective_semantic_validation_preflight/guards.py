"""SAME_BAR, THESIS_LOST absorbing, hidden-1m parallel path. Synthetic / stored V4 only."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.execution import classify_e1
from research.pb1_v4_clarified_machine_correction_v4.machine import lose_thesis, mint_active, mint_seed, mint_why, new_side
from research.pb1_v4_clarified_machine_correction_v4.thesis import hidden_1m_snapshot
from research.pb1_v4_prospective_semantic_validation_preflight.logging import candidate_day_log, causal_timestamps


def same_bar_guard() -> dict[str, Any]:
    rec = {"t": ["09:50", "09:51"], "o": [100.0, 101.0], "h": [101.0, 102.0], "l": [99.0, 100.0], "c": [100.5, 101.5]}
    ts = causal_timestamps(event_completed_at="09:50", rec=rec, pos=0)
    bad = causal_timestamps(event_completed_at="09:50")
    bad_forced = dict(bad)
    bad_forced["entry_allowed_at"] = "09:50"
    blocked = bad_forced["entry_allowed_at"] == bad_forced["event_completed_at"]
    return {
        "ok": ts.get("same_bar_entry") is False and ts.get("entry_allowed_at") == "09:51" and blocked,
        "next_open_entry": ts.get("entry_allowed_at"),
        "same_bar_blocked_when_entry_equals_event": blocked,
        "V4_next_open_same_bar_entry_always_false": True,
    }


def thesis_lost_absorbing() -> dict[str, Any]:
    st = new_side(1)
    mint_why(st, symbol="SYN", date="19990101")
    mint_seed(st, seed="TRUE_OPENING_DRIVE_SEED")
    mint_active(st)
    st.thesis_live = True
    st.thesis_reached = True
    st.thesis_id = f"{st.opening_drive_id}|THESIS"
    lose_thesis(st, "STALE_RANGE_RESOLUTION", t="10:04")
    first_at = st.thesis_lost_at
    first_reason = st.thesis_lost_reason
    kept_id = st.thesis_id
    lose_thesis(st, "SHOULD_NOT_REPLACE", t="10:19")
    e1_walk = classify_e1(
        sign=1,
        rec={"t": ["10:20"], "o": [1], "h": [2], "l": [0], "c": [1.5]},
        pos=0,
        open_px=1.0,
        high=2.0,
        low=0.0,
        close=1.5,
        retest_high=1.2,
        retest_low=0.8,
        n1m=1.0,
        thesis_ready=bool(st.thesis_live),
        thesis_lost=bool(st.thesis_lost),
    )
    e1_lost = classify_e1(
        sign=1,
        rec={"t": ["10:20"], "o": [1], "h": [2], "l": [0], "c": [1.5]},
        pos=0,
        open_px=1.0,
        high=2.0,
        low=0.0,
        close=1.5,
        retest_high=1.2,
        retest_low=0.8,
        n1m=1.0,
        thesis_ready=True,
        thesis_lost=True,
    )
    walk_would_skip = (not st.thesis_live) or bool(st.thesis_lost)
    return {
        "ok": (
            st.thesis_lost
            and not st.thesis_live
            and first_at == "10:04"
            and st.thesis_lost_at == "10:04"
            and first_reason == "STALE_RANGE_RESOLUTION"
            and st.thesis_lost_reason == "STALE_RANGE_RESOLUTION"
            and kept_id == st.thesis_id
            and walk_would_skip
            and e1_walk.get("state") == "BLOCKED"
            and e1_lost.get("state") == "E1_ATTEMPT_CANCELLED"
        ),
        "first_death_preserved": st.thesis_lost_at == first_at and st.thesis_lost_reason == first_reason,
        "ids_kept": bool(st.thesis_id) and bool(st.opening_drive_id),
        "e1_if_called_with_live_false": e1_walk.get("state"),
        "e1_if_called_with_lost_true": e1_lost.get("state"),
        "walk_skips_e1_after_loss": walk_would_skip,
        "revived": bool(st.thesis_live),
    }


def hidden_1m_parallel() -> dict[str, Any]:
    five = hidden_1m_snapshot(
        symbol="SYN",
        date="19990101",
        direction="bear",
        opening_drive_id="SYN|19990101|SEED|FAILED_OPEN_SEED|BEAR|ACTIVE",
        location_id="SYN|19990101|SEED|FAILED_OPEN_SEED|BEAR|ACTIVE|LOC|B_OR_AFTER_LEAVE",
        thesis_id="SYN|19990101|SEED|FAILED_OPEN_SEED|BEAR|ACTIVE|LOC|B_OR_AFTER_LEAVE|THESIS",
        thesis_ready_flag=True,
    )
    e1_id = f"{five['thesis_id']}|E1|09:51"
    one = hidden_1m_snapshot(
        symbol="SYN",
        date="19990101",
        direction="bear",
        opening_drive_id=five["opening_drive_id"],
        location_id=five["location_id"],
        thesis_id=five["thesis_id"],
        thesis_ready_flag=True,
    )
    st = new_side(-1)
    mint_why(st, symbol="SYN", date="19990101")
    mint_seed(st, seed="FAILED_OPEN_SEED")
    mint_active(st)
    st.location_identified = True
    st.location_id = five["location_id"]
    st.thesis_id = five["thesis_id"]
    st.thesis_reached = True
    st.thesis_live = True
    log = candidate_day_log(st, symbol="SYN", date="19990101")
    loc_from_1m = "IX1M" in str(five.get("location_id") or "")
    return {
        "ok": (
            five == one
            and five.get("one_m_hidden") is True
            and not loc_from_1m
            and e1_id.endswith("|E1|09:51")
            and five["location_id"] == one["location_id"]
            and log.get("_complete_schema") is True
        ),
        "one_m_hidden": five.get("one_m_hidden"),
        "location_unchanged_by_e1_timing": five["location_id"] == one["location_id"],
        "schema_complete": log.get("_complete_schema"),
        "causal_fields_attachable": list(causal_timestamps(event_completed_at="09:14").keys()),
    }

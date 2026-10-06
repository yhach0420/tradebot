"""Knowability of last-event clusters. Gap<=10 join window is inclusive. Absence must be observed."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, parse_hhmm
from research.r11_online_episode_causality_audit_v1 import ENTRY_CUTOFF, EPISODE_GAP_MIN


def to_min(hhmm: str | None) -> int | None:
    p = parse_hhmm(str(hhmm or ""))
    if p is None:
        return None
    return p[0] * 60 + p[1]


def gap_min(a: str | None, b: str | None) -> int | None:
    am, bm = to_min(a), to_min(b)
    if am is None or bm is None:
        return None
    return int(bm - am)


def joins(prev_t: str, next_t: str, *, gap: int = EPISODE_GAP_MIN) -> bool:
    g = gap_min(prev_t, next_t)
    return g is not None and 0 <= g <= int(gap)


def earliest_cluster_last_knowable(
    last_event_time: str,
    *,
    next_same_symbol_event_time: str | None,
    emission_end: str = ENTRY_CUTOFF,
    gap: int = EPISODE_GAP_MIN,
) -> dict[str, Any]:
    """Last event is knowable only after the inclusive join window has elapsed with no joiner.

    An event at last+gap still joins (gap<=10). After last+gap is fully observed with no
    joining onset, no later event can join. If the generator cannot emit after emission_end
    (14:50), the window truncates there.
    """
    last_m = to_min(last_event_time)
    if last_m is None:
        return {"ok": False, "earliest_time_cluster_last_is_knowable": None}
    if next_same_symbol_event_time and joins(last_event_time, next_same_symbol_event_time, gap=gap):
        return {
            "ok": False,
            "is_cluster_last": False,
            "earliest_time_cluster_last_is_knowable": None,
            "reason": "next_event_still_joins",
        }
    join_deadline = hhmm_add(last_event_time, int(gap))
    end_m = to_min(emission_end)
    dl_m = to_min(join_deadline)
    if end_m is not None and dl_m is not None and dl_m > end_m:
        knowable = emission_end if last_m < end_m else last_event_time
        truncated = True
    else:
        knowable = join_deadline
        truncated = False
    lag = gap_min(last_event_time, knowable)
    lookahead = bool(knowable is not None and last_event_time < str(knowable))
    return {
        "ok": True,
        "is_cluster_last": True,
        "earliest_time_cluster_last_is_knowable": knowable,
        "CAUSAL_KNOWABILITY_LAG_MIN": lag,
        "LOOKAHEAD_VIOLATION": lookahead,
        "join_window_truncated_by_cutoff": truncated,
        "known_at_own_event_timestamp": not lookahead,
    }

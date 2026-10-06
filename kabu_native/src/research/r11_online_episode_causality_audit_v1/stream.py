"""True streaming last-event vs first-event refractory. No future cluster endpoint at emit time."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.r11_online_episode_causality_audit_v1 import ENTRY_CUTOFF, EPISODE_GAP_MIN
from research.r11_online_episode_causality_audit_v1.knowability import earliest_cluster_last_knowable, gap_min, to_min


def _sort_key(e: dict[str, Any]) -> tuple:
    return (str(e.get("date") or ""), str(e.get("event_time") or ""), str(e.get("symbol") or ""), str(e.get("event_family") or ""))


def stream_last_event_delayed(
    events: list[dict[str, Any]],
    *,
    gap: int = EPISODE_GAP_MIN,
    emission_end: str = ENTRY_CUTOFF,
) -> list[dict[str, Any]]:
    """Clock-causal last-event: emit only after the join window is observed empty.

    decision_time is the knowable time, never the historical last-event time assigned retroactively.
    """
    by_sym: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_sym[(str(e.get("date")), str(e.get("symbol")))].append(e)
    out: list[dict[str, Any]] = []
    for (day, _sym), xs in by_sym.items():
        xs = sorted(xs, key=_sort_key)
        pending: dict[str, Any] | None = None
        for i, e in enumerate(xs):
            t = str(e.get("event_time") or "")
            if pending is None:
                pending = e
                continue
            pt = str(pending.get("event_time") or "")
            g = gap_min(pt, t)
            if g is not None and g <= int(gap):
                pending = e
                continue
            nxt = t
            meta = earliest_cluster_last_knowable(pt, next_same_symbol_event_time=nxt, emission_end=emission_end, gap=gap)
            rec = dict(pending)
            rec["streaming_role"] = "LAST_EVENT_CAUSAL_DELAYED"
            rec["original_trigger_event_time"] = pt
            rec["original_decision_time"] = pt
            rec["decision_time"] = meta.get("earliest_time_cluster_last_is_knowable")
            rec["cluster_last_event_time"] = pt
            rec["next_same_symbol_event_time"] = nxt
            rec["earliest_time_cluster_last_is_knowable"] = meta.get("earliest_time_cluster_last_is_knowable")
            rec["CAUSAL_KNOWABILITY_LAG_MIN"] = meta.get("CAUSAL_KNOWABILITY_LAG_MIN")
            rec["LOOKAHEAD_VIOLATION"] = True
            rec["emitted_at_original_decision_time"] = False
            out.append(rec)
            pending = e
        if pending is not None:
            pt = str(pending.get("event_time") or "")
            meta = earliest_cluster_last_knowable(pt, next_same_symbol_event_time=None, emission_end=emission_end, gap=gap)
            rec = dict(pending)
            rec["streaming_role"] = "LAST_EVENT_CAUSAL_DELAYED"
            rec["original_trigger_event_time"] = pt
            rec["original_decision_time"] = pt
            rec["decision_time"] = meta.get("earliest_time_cluster_last_is_knowable")
            rec["cluster_last_event_time"] = pt
            rec["next_same_symbol_event_time"] = None
            rec["earliest_time_cluster_last_is_knowable"] = meta.get("earliest_time_cluster_last_is_knowable")
            rec["CAUSAL_KNOWABILITY_LAG_MIN"] = meta.get("CAUSAL_KNOWABILITY_LAG_MIN")
            rec["LOOKAHEAD_VIOLATION"] = bool(meta.get("LOOKAHEAD_VIOLATION"))
            rec["emitted_at_original_decision_time"] = bool(meta.get("known_at_own_event_timestamp"))
            out.append(rec)
        _ = day
    out.sort(key=_sort_key)
    return out


def stream_first_event_refractory(
    events: list[dict[str, Any]],
    *,
    gap: int = EPISODE_GAP_MIN,
) -> list[dict[str, Any]]:
    """First onset starts immediately; suppress new same-symbol starts for `gap` minutes.

    Online-causal. Not the frozen last-event strategy.
    """
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_day[str(e.get("date"))].append(e)
    out: list[dict[str, Any]] = []
    for _day, xs in by_day.items():
        xs = sorted(xs, key=_sort_key)
        refractory_until: dict[str, int] = {}
        for e in xs:
            t = str(e.get("event_time") or "")
            tm = to_min(t)
            if tm is None:
                continue
            sym = str(e.get("symbol") or "")
            until = refractory_until.get(sym)
            if until is not None and tm <= until:
                continue
            rec = dict(e)
            rec["streaming_role"] = "FIRST_EVENT_REFRACTORY"
            rec["decision_time"] = t
            rec["original_decision_time"] = t
            rec["emitted_at_original_decision_time"] = True
            rec["LOOKAHEAD_VIOLATION"] = False
            rec["CAUSAL_KNOWABILITY_LAG_MIN"] = 0
            out.append(rec)
            refractory_until[sym] = tm + int(gap)
    out.sort(key=_sort_key)
    return out


def cand_key_full(row: dict[str, Any]) -> tuple:
    return (
        str(row.get("date") or ""),
        str(row.get("symbol") or ""),
        str(row.get("feature_bar") or ""),
        str(row.get("available_at") or ""),
        str(row.get("decision_time") or row.get("event_time") or ""),
    )


def cand_key_event(row: dict[str, Any]) -> tuple:
    return (str(row.get("date") or ""), str(row.get("symbol") or ""), str(row.get("original_trigger_event_time") or row.get("event_time") or ""))

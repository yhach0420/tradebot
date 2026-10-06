"""Join frozen 23 raw-event descriptors. No 0-event silent backfill. No window search."""
from __future__ import annotations

from typing import Any

from research.canonical_entry_performance_rebase.analyze import _bare, _f, row_key, session_of
from research.raw_event_prediction_probe import RAW_DESCRIPTORS
from research.am_raw_event_incremental_selection import RAW_EVENT_AVAILABLE


def harvest_key(r: dict[str, Any]) -> str:
    return f"{r.get('date')}|{r.get('anchor')}|{_bare(r.get('symbol'))}"


def _n_events(h: dict[str, Any]) -> int:
    n = h.get("n_events")
    if n is None or n == "":
        n = h.get("event_count_180s")
    try:
        return int(n or 0)
    except (TypeError, ValueError):
        return 0


def join_raw(rows: list[dict[str, Any]], harvest: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    idx: dict[str, dict[str, Any]] = {}
    for h in harvest:
        idx[harvest_key(h)] = h
    miss = 0
    available_n = 0
    zero_event_n = 0
    out = []
    for r in rows:
        rec = dict(r)
        h = idx.get(row_key(rec))
        if h is None:
            miss += 1
            rec[RAW_EVENT_AVAILABLE] = 0.0
            rec["EVENT_AVAILABLE"] = False
            rec["n_events"] = None
            for k in RAW_DESCRIPTORS:
                rec[k] = None
            out.append(rec)
            continue
        n_evt = _n_events(h)
        rec["n_events"] = n_evt
        if n_evt <= 0:
            zero_event_n += 1
            rec[RAW_EVENT_AVAILABLE] = 0.0
            rec["EVENT_AVAILABLE"] = False
            for k in RAW_DESCRIPTORS:
                rec[k] = None
            out.append(rec)
            continue
        available_n += 1
        rec[RAW_EVENT_AVAILABLE] = 1.0
        rec["EVENT_AVAILABLE"] = True
        for k in RAW_DESCRIPTORS:
            rec[k] = _f(h.get(k))
        out.append(rec)
    meta = {
        "JOIN_MISS_N": int(miss),
        "RAW_EVENT_AVAILABLE_N": int(available_n),
        "RAW_EVENT_ZERO_N": int(zero_event_n),
        "HARVEST_N": len(harvest),
        "LABELED_N": len(rows),
        "PM_HARVEST_N": sum(1 for h in harvest if session_of(h) != "AM"),
    }
    return out, meta

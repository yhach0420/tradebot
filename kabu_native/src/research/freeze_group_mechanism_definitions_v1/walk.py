"""Replay frozen walk. VWAP_RECLAIM events kept; other sequences dropped after identical emission. No rule change."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.behavior_group_sequence_mechanism_v1.engine import process_day
from research.cause_first_mechanism_discovery_v1.panel import load_minutes


def walk_vwap_reclaim(
    *,
    symbols: list[str],
    sector_of: dict[str, str],
    allowed: set[str],
    forbidden: set[str],
    date_to_block: dict[str, str],
) -> dict[str, Any]:
    print(f"LOAD_MINUTES symbols={len(symbols)} discovery_days={len(allowed)} vwap_reclaim_keep_only", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes.empty:
        return {"ok": False, "events": [], "daily": []}
    if minutes["date"].isin(list(forbidden)).any():
        raise RuntimeError("forbidden_partition_loaded")
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    events: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    prev_close: dict[str, float] = {}
    n_days = int(minutes["date"].nunique())
    for i, (day, g) in enumerate(minutes.groupby("date", sort=True), start=1):
        pack = process_day(date=str(day), g=g, sector_of=sector_of, date_to_block=date_to_block, prev_close=prev_close)
        events.extend([e for e in pack["events"] if e.get("sequence") == "VWAP_RECLAIM"])
        daily.extend(pack["daily"])
        prev_close.update(pack["last_close"])
        if i % 20 == 0 or i == n_days:
            print(f"DAY {i}/{n_days} vwap_events={len(events)}", flush=True)
    by_seq: dict[str, int] = defaultdict(int)
    for e in events:
        by_seq[str(e.get("sequence"))] += 1
    return {
        "ok": True,
        "day_n": n_days,
        "event_n": len(events),
        "events": events,
        "daily": daily,
        "by_sequence": dict(by_seq),
        "used_5m_grid": False,
        "bar_start": True,
        "atlas_classifier_used": False,
        "rule_changed": False,
        "sequences_dropped_after_emission_only": True,
    }

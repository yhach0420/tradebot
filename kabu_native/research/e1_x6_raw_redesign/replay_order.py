"""Availability-order replay contract (Phase A-R1 §3) + canonical regression audit.

The ONLY replay order for Phase B is availability (ingress) order:
- events are processed in ingress/availability timestamp order;
- ties: source sequence if present, else a fixed event key (symbol, then the
  canonical unique_key);
- NEVER re-sorted by source market timestamp;
- late-arriving events are never inserted into past grids;
- source timestamps are used for freshness only, never as availability;
- feature ledger values fixed before a late event arrived are immutable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

REPLAY_ORDER_CONTRACT = {
    "order": "ingress/availability timestamp ascending",
    "tie_break": "source sequence if present, else fixed event key (symbol, unique_key)",
    "no_source_time_sort": True,
    "no_backfill_into_past_grids": True,
    "source_ts_role": "freshness gate only (usable_ts = max(ingress, source)); never availability",
    "late_event_immutability": "feature ledger finalized at grid t never changes on later arrivals",
}


def availability_sort_key(ev: Any) -> tuple:
    """Deterministic availability key for canonical events (read-only use)."""
    seq = getattr(ev, "sequence", None)
    return (
        ev.ts.timestamp() if hasattr(ev.ts, "timestamp") else float(ev.ts),
        seq if seq is not None else 0,
        str(getattr(ev, "symbol", "")),
        str(getattr(ev, "unique_key", "")),
    )


def audit_canonical_regressions(native_root: Path, day: str) -> dict[str, Any]:
    """Count source-timestamp regressions in stored canonical order (read-only).

    Reported SEPARATELY from raw per-file ingress inversions. The stored
    canonical order is the normalizer's file order; regressions here mean the
    source market timestamps are NOT monotone in that order, which is exactly
    why availability order is the frozen replay order.
    """
    import small_paper.e1_x5_canonical_replay as cr

    from .source_manifest import canonical_cache_dir

    events, report = cr.normalize_day(native_root, day, cache_dir=canonical_cache_dir(),
                                      use_cache=True)
    regr = 0
    prev = None
    for e in events:
        t = e.ts
        if prev is not None and t < prev:
            regr += 1
        prev = t
    n = len(events)
    del events
    return {
        "day": day,
        "canonical_events": n,
        "canonical_ts_regressions_stored_order": regr,
        "normalizer_reported_regressions": int(
            getattr(report, "timestamp_regressions_in_file_order", 0) or 0
        ),
        "note": (
            "separate column from raw per-file ingress inversions (which were 0); "
            "'timestamp inversions = 0' in the A-report referred to RAW INGRESS order"
        ),
    }

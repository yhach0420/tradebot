"""Fanout, burst, capacity/duplicate simulation — no future ranking."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import HOLD_SEC_FOR_CAPACITY, HORIZONS, LOT_QTY, POSITION_CAP, WAIT_SEC


def dist_stats(xs: list[float]) -> dict[str, Any]:
    a = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p90": None, "p95": None, "p99": None, "max": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p90": float(np.quantile(a, 0.90)),
        "p95": float(np.quantile(a, 0.95)),
        "p99": float(np.quantile(a, 0.99)),
        "max": float(np.max(a)),
    }


def order_fanout(events: list[dict]) -> dict[str, Any]:
    """Eligible pending orders per fixed clock timestamp."""
    by_ts: dict[tuple, int] = defaultdict(int)
    for e in events:
        # every signal places a pending order at order_time (research)
        key = (e["date"], float(e["order_time"]))
        by_ts[key] += 1
    counts = [float(v) for v in by_ts.values()]
    return {
        "n_timestamps": len(counts),
        "orders_per_timestamp": dist_stats(counts),
        "note": "all neutral eligible signals place Buy1 limit for wait=1s",
    }


def fill_burst(fills: list[dict]) -> dict[str, Any]:
    """Burst counts by observed fill timestamp resolution (no interpolation)."""
    if not fills:
        return {"status": "NO_FILLS"}
    times = sorted(float(f["fill_time"]) for f in fills)

    def _burst(window: float) -> dict[str, Any]:
        # sliding: for each fill, count fills in [t, t+window]
        peaks = []
        j = 0
        for i, t in enumerate(times):
            while j < len(times) and times[j] <= t + window + 1e-12:
                j += 1
            peaks.append(float(j - i))
        return dist_stats(peaks)

    # also exact same timestamp collisions
    by_exact: dict[float, int] = defaultdict(int)
    for t in times:
        by_exact[round(t, 6)] += 1  # microsecond-ish bucket for float
    exact_counts = [float(v) for v in by_exact.values()]

    return {
        "n_fills": len(fills),
        "same_timestamp_max": float(max(exact_counts)) if exact_counts else 0.0,
        "window_100ms": _burst(0.10),
        "window_250ms": _burst(0.25),
        "window_500ms": _burst(0.50),
        "window_1s": _burst(1.0),
        "max_simultaneous_1s": (_burst(1.0) or {}).get("max"),
        "p95_burst_1s": (_burst(1.0) or {}).get("p95"),
        "p99_burst_1s": (_burst(1.0) or {}).get("p99"),
        "no_interpolation": True,
    }


def pending_risk_audit(events: list[dict]) -> dict[str, Any]:
    """If all pending at a clock filled simultaneously, exposure vs cap."""
    by_ts: dict[tuple, list[dict]] = defaultdict(list)
    for e in events:
        by_ts[(e["date"], float(e["order_time"]))].append(e)
    over = 0
    samples = []
    for key, group in by_ts.items():
        n = len(group)
        # potential simultaneous fills (worst case all fill)
        potential_positions = n  # each 100 shares one position slot
        notional = []
        for g in group:
            px = g.get("limit_price") or g.get("ask0") or 0.0
            if px:
                notional.append(float(px) * LOT_QTY)
        samples.append({
            "date": key[0],
            "order_time": key[1],
            "pending_count": n,
            "potential_positions_if_all_fill": n,
            "exceeds_cap": n > POSITION_CAP,
            "potential_notional_yen_sum": float(sum(notional)) if notional else None,
        })
        if n > POSITION_CAP:
            over += 1
    return {
        "position_cap": POSITION_CAP,
        "lot_qty": LOT_QTY,
        "timestamps_exceeding_cap_if_all_fill": over,
        "share_timestamps_over_cap": float(over / len(by_ts)) if by_ts else None,
        "pending_count_stats": dist_stats([float(s["pending_count"]) for s in samples]),
        "note": "pending simultaneous fill is why capacity simulation is required",
        "sample_over": [s for s in samples if s["exceeds_cap"]][:15],
    }


def simulate_capacity(events: list[dict]) -> dict[str, Any]:
    """
    Causal capacity replay:
      - place pending at order_time
      - cancel at cancel_time if unfilled
      - on fill: accept if under cap and no same-symbol open/pending conflict
      - deterministic tie-break: (fill_time, symbol) — NEVER future return
      - exit at fill_time + HOLD_SEC (fixed horizon occupancy)
    Mutates copies of event dicts with accepted / CAPACITY_BLOCKED / DUPLICATE_BLOCKED.
    """
    rows = [dict(e) for e in events]
    for r in rows:
        r["accepted"] = False
        r["CAPACITY_BLOCKED"] = False
        r["DUPLICATE_BLOCKED"] = False
        r["block_reason"] = None

    # index
    by_id = {(r["date"], r["symbol"], float(r["signal_time"])): r for r in rows}

    # Timeline events
    # PENDING_OPEN at order_time, PENDING_CANCEL at cancel_time,
    # FILL at fill_time (if filled), EXIT at exit_time for accepted
    timeline: list[tuple[float, int, str, tuple]] = []
    # priority: EXIT(0) before FILL(1) before CANCEL(2) before OPEN(3) at same time? 
    # At fill instant we need current open count after exits at same time.
    # Order: EXIT first, then FILL, then CANCEL, then PENDING_OPEN
    PRI = {"EXIT": 0, "FILL": 1, "CANCEL": 2, "OPEN": 3}

    for r in rows:
        key = (r["date"], r["symbol"], float(r["signal_time"]))
        timeline.append((float(r["order_time"]), PRI["OPEN"], "OPEN", key))
        timeline.append((float(r["cancel_time"]), PRI["CANCEL"], "CANCEL", key))
        if r.get("filled") and r.get("fill_time") is not None:
            timeline.append((float(r["fill_time"]), PRI["FILL"], "FILL", key))

    timeline.sort(key=lambda x: (x[0], x[1], x[3][1]))  # time, kind, symbol

    pending: set[tuple] = set()  # keys
    open_pos: dict[tuple, float] = {}  # key -> exit_time
    # also track open by (date, symbol) for duplicate
    open_sym: dict[tuple[str, str], tuple] = {}
    pending_sym: dict[tuple[str, str], tuple] = {}

    accepted = 0
    cap_blocked = 0
    dup_blocked = 0
    max_open = 0

    # schedule exits when accepted
    exit_scheduled: set[tuple] = set()

    def _add_exit(key, exit_t):
        if key in exit_scheduled:
            return
        exit_scheduled.add(key)
        timeline.append((float(exit_t), PRI["EXIT"], "EXIT", key))
        # keep sorted — we'll process with a heap-like re-sort periodically
        # For simplicity re-sort remaining: use index walk with insert
        timeline.sort(key=lambda x: (x[0], x[1], x[3][1]))

    # Process with index that may grow — use while i
    i = 0
    # re-build as we may insert exits; safer: two-pass
    # Pass1: collect all potential fills; Pass2 won't work for dynamic exits.
    # Use heap
    import heapq
    heap: list[tuple] = []
    for item in timeline:
        heapq.heappush(heap, item)

    while heap:
        t, pri, kind, key = heapq.heappop(heap)
        r = by_id.get(key)
        if r is None:
            continue
        day, sym, _ = key
        sym_key = (day, sym)

        if kind == "OPEN":
            pending.add(key)
            # if already pending/open same symbol — still place? Spec: audit duplication.
            # We allow pending record but block fill later. Track first pending.
            if sym_key not in pending_sym and sym_key not in open_sym:
                pending_sym[sym_key] = key
            continue

        if kind == "CANCEL":
            pending.discard(key)
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            continue

        if kind == "EXIT":
            if key in open_pos:
                open_pos.pop(key)
                if open_sym.get(sym_key) == key:
                    open_sym.pop(sym_key, None)
            continue

        if kind == "FILL":
            if key not in pending and not r.get("filled"):
                continue
            # fill occurs only if still pending (not cancelled) — fill_time <= cancel_time
            if float(r["fill_time"]) > float(r["cancel_time"]) + 1e-12:
                continue
            if key not in pending:
                # already cancelled somehow
                continue

            # duplicate: already holding or another pending for symbol that isn't this order
            if sym_key in open_sym:
                r["DUPLICATE_BLOCKED"] = True
                r["block_reason"] = "SAME_SYMBOL_OPEN"
                dup_blocked += 1
                continue
            other_pending = pending_sym.get(sym_key)
            if other_pending is not None and other_pending != key:
                # another pending exists — block this fill (deterministic: keep earlier order)
                r["DUPLICATE_BLOCKED"] = True
                r["block_reason"] = "SAME_SYMBOL_PENDING"
                dup_blocked += 1
                continue

            if len(open_pos) >= POSITION_CAP:
                r["CAPACITY_BLOCKED"] = True
                r["block_reason"] = "CAPACITY_BLOCKED"
                cap_blocked += 1
                continue

            # accept
            r["accepted"] = True
            accepted += 1
            exit_t = float(r["fill_time"]) + HOLD_SEC_FOR_CAPACITY
            r["exit_time_capacity"] = exit_t
            open_pos[key] = exit_t
            open_sym[sym_key] = key
            pending.discard(key)
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            max_open = max(max_open, len(open_pos))
            heapq.heappush(heap, (exit_t, PRI["EXIT"], "EXIT", key))

    return {
        "events": rows,
        "position_cap": POSITION_CAP,
        "hold_sec": HOLD_SEC_FOR_CAPACITY,
        "same_symbol_policy": "no_overlap_replace_block",
        "tie_break": "(fill_time, symbol) causal - no future return ranking",
        "accepted_fills": accepted,
        "capacity_blocked": cap_blocked,
        "duplicate_blocked": dup_blocked,
        "raw_fills": sum(1 for r in rows if r.get("filled")),
        "blocked_share": (
            float((cap_blocked + dup_blocked) / sum(1 for r in rows if r.get("filled")))
            if any(r.get("filled") for r in rows) else None
        ),
        "max_open_observed": max_open,
    }

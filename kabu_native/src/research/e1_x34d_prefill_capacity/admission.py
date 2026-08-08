"""Pre-fill hard-cap admission: pending reserves a slot; open+pending <= CAP."""
from __future__ import annotations

import hashlib
import heapq
from collections import defaultdict
from typing import Any, Callable

from . import (
    HASH_SALT,
    LOT_QTY,
    OCCUPANCY_PROXY_600S,
    ORDER_ASC,
    ORDER_DESC,
    ORDER_HASH,
    POSITION_CAP,
    WAIT_SEC,
)


def _sort_key(mode: str) -> Callable[[dict], tuple]:
    if mode == ORDER_ASC:
        return lambda e: (str(e["symbol"]), float(e["signal_time"]))
    if mode == ORDER_DESC:
        return lambda e: (str(e["symbol"]), float(e["signal_time"]))
    if mode == ORDER_HASH:
        def _h(e: dict) -> tuple:
            h = hashlib.md5(f"{e['symbol']}|{HASH_SALT}".encode()).hexdigest()
            return (h, str(e["symbol"]), float(e["signal_time"]))
        return _h
    raise ValueError(mode)


def _reverse_for_desc(mode: str) -> bool:
    return mode == ORDER_DESC


def simulate_prefill(
    events: list[dict],
    *,
    order_mode: str = ORDER_ASC,
) -> dict[str, Any]:
    """
    Pre-fill hard capacity:
      available_slots = CAP - open - pending
      admit only if slots > 0 and not duplicate OPEN/PENDING symbol
      pending reserves 1 slot until fill or 1s expiry
      fill only if admitted (uses X34C fill evidence timestamps)
      occupancy proxy: OCCUPANCY_PROXY_600S after fill
      NEVER uses future returns for ranking
    """
    rows = [dict(e) for e in events]
    for r in rows:
        r["admitted"] = False
        r["admission_blocked"] = False
        r["expired"] = False
        r["prefill_filled"] = False
        r["accepted"] = False  # filled under prefill (same as prefill_filled for economics)
        r["DUPLICATE_BLOCKED"] = False
        r["CAPACITY_BLOCKED"] = False  # admission blocked by slots
        r["block_reason"] = None
        r["state_path"] = []

    by_id = {(r["date"], r["symbol"], float(r["signal_time"])): r for r in rows}

    # Group signals by (date, signal_time) for batch admission
    by_clock: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_clock[(r["date"], float(r["signal_time"]))].append(r)

    key_fn = _sort_key(order_mode)
    rev = _reverse_for_desc(order_mode)

    # Timeline
    PRI = {"EXIT": 0, "FILL": 1, "EXPIRE": 2, "ADMIT_BATCH": 3}
    heap: list[tuple] = []

    for (day, t0), group in by_clock.items():
        heapq.heappush(heap, (float(t0), PRI["ADMIT_BATCH"], "ADMIT_BATCH", (day, float(t0))))

    # Also need FILL/EXPIRE for admitted — pushed when admitted
    open_pos: dict[tuple, float] = {}  # key -> exit_time
    pending: dict[tuple, dict] = {}  # key -> row
    open_sym: dict[tuple[str, str], tuple] = {}
    pending_sym: dict[tuple[str, str], tuple] = {}

    hard_cap_violations = 0
    max_open_pending = 0
    admitted_n = 0
    admission_blocked_n = 0
    dup_n = 0
    expired_n = 0
    fill_n = 0
    notional_samples = []

    def _exposure() -> int:
        return len(open_pos) + len(pending)

    def _assert_cap(at: float) -> None:
        nonlocal hard_cap_violations, max_open_pending
        exp = _exposure()
        max_open_pending = max(max_open_pending, exp)
        if exp > POSITION_CAP:
            hard_cap_violations += 1

    while heap:
        t, pri, kind, payload = heapq.heappop(heap)

        if kind == "ADMIT_BATCH":
            day, t0 = payload
            group = by_clock[(day, t0)]
            ordered = sorted(group, key=key_fn, reverse=rev)
            for r in ordered:
                key = (r["date"], r["symbol"], float(r["signal_time"]))
                sym_key = (r["date"], r["symbol"])

                if sym_key in open_sym or sym_key in pending_sym:
                    r["DUPLICATE_BLOCKED"] = True
                    r["admission_blocked"] = True
                    r["block_reason"] = "SAME_SYMBOL_OPEN_OR_PENDING"
                    dup_n += 1
                    r["state_path"].append("DUPLICATE_BLOCKED")
                    continue

                avail = POSITION_CAP - _exposure()
                if avail <= 0:
                    r["CAPACITY_BLOCKED"] = True
                    r["admission_blocked"] = True
                    r["block_reason"] = "NO_AVAILABLE_SLOT"
                    admission_blocked_n += 1
                    r["state_path"].append("CAPACITY_BLOCKED")
                    continue

                # Admit → PENDING (reserves slot)
                r["admitted"] = True
                r["state_path"].append("PENDING")
                pending[key] = r
                pending_sym[sym_key] = key
                admitted_n += 1
                _assert_cap(t0)

                expire_t = float(t0) + WAIT_SEC
                heapq.heappush(heap, (expire_t, PRI["EXPIRE"], "EXPIRE", key))

                # Schedule fill only if original evidence says filled within wait
                if r.get("filled") and r.get("fill_time") is not None:
                    ft = float(r["fill_time"])
                    if ft <= expire_t + 1e-12:
                        heapq.heappush(heap, (ft, PRI["FILL"], "FILL", key))

                px = r.get("limit_price") or r.get("ask0") or 0.0
                notional_samples.append({
                    "t": t0,
                    "open": len(open_pos),
                    "pending": len(pending),
                    "potential_notional_yen": float(px) * LOT_QTY if px else None,
                })
            continue

        if kind == "FILL":
            key = payload
            r = by_id.get(key)
            if r is None or key not in pending:
                continue  # expired already or not pending
            # Convert pending → open
            pending.pop(key)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)

            exit_t = float(r["fill_time"]) + OCCUPANCY_PROXY_600S
            open_pos[key] = exit_t
            open_sym[sym_key] = key
            r["prefill_filled"] = True
            r["accepted"] = True
            r["state_path"].append("FILLED")
            fill_n += 1
            _assert_cap(float(r["fill_time"]))
            heapq.heappush(heap, (exit_t, PRI["EXIT"], "EXIT", key))
            continue

        if kind == "EXPIRE":
            key = payload
            r = by_id.get(key)
            if r is None or key not in pending:
                continue  # already filled
            pending.pop(key)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            r["expired"] = True
            r["state_path"].append("EXPIRED")
            expired_n += 1
            _assert_cap(float(r["cancel_time"]))
            continue

        if kind == "EXIT":
            key = payload
            if key in open_pos:
                open_pos.pop(key)
                r = by_id[key]
                sym_key = (r["date"], r["symbol"])
                if open_sym.get(sym_key) == key:
                    open_sym.pop(sym_key, None)
                r["state_path"].append("EXIT_PROXY")
            _assert_cap(t)
            continue

    return {
        "events": rows,
        "order_mode": order_mode,
        "position_cap": POSITION_CAP,
        "occupancy_proxy_sec": OCCUPANCY_PROXY_600S,
        "occupancy_label": "OCCUPANCY_PROXY_600S",
        "pending_reserves_slot": True,
        "wait_sec": WAIT_SEC,
        "signals": len(rows),
        "orders_admitted": admitted_n,
        "admission_blocked": admission_blocked_n,
        "duplicate_blocked": dup_n,
        "expired_orders": expired_n,
        "raw_fills_if_admitted_evidence": fill_n,
        "accepted_fills": fill_n,
        "hard_cap_violations": hard_cap_violations,
        "max_open_plus_pending": max_open_pending,
        "fill_rate_per_admitted": float(fill_n / admitted_n) if admitted_n else None,
        "fill_rate_per_signal": float(fill_n / len(rows)) if rows else None,
        "notional_sample_n": len(notional_samples),
        "potential_notional_mean_yen": (
            float(sum(x["potential_notional_yen"] for x in notional_samples if x["potential_notional_yen"]) / max(1, sum(1 for x in notional_samples if x["potential_notional_yen"])))
            if notional_samples else None
        ),
        "no_future_ranking": True,
        "no_post_fill_retroactive_acceptance": True,
    }

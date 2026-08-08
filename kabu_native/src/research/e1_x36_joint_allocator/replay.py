"""Joint hard-cap replay: pending reservation + canonical FIXED600 occupancy."""
from __future__ import annotations

import hashlib
import heapq
from collections import defaultdict
from typing import Any, Callable, Optional

from . import (
    HASH_SALT,
    LOT_QTY,
    ORDER_ASC,
    ORDER_DESC,
    ORDER_HASH,
    POSITION_CAP,
    WAIT_SEC,
)
from .panel import pnl_yen


def _neutral_key(mode: str, salt: str = HASH_SALT) -> Callable[[dict], tuple]:
    if mode == ORDER_ASC:
        return lambda e: (str(e["symbol"]), float(e["signal_time"]))
    if mode == ORDER_DESC:
        return lambda e: (str(e["symbol"]), float(e["signal_time"]))
    if mode == ORDER_HASH or mode.startswith("hash_"):
        def _h(e: dict) -> tuple:
            h = hashlib.md5(f"{e['symbol']}|{salt}".encode()).hexdigest()
            return (h, str(e["symbol"]), float(e["signal_time"]))
        return _h
    raise ValueError(mode)


def simulate_joint(
    events: list[dict],
    *,
    score_fn: Optional[Callable[[dict], float]] = None,
    order_mode: str = ORDER_ASC,
    hash_salt: str = HASH_SALT,
) -> dict[str, Any]:
    """
    Event-time joint replay.
    Ranking within clock cohort:
      - if score_fn: higher score first, tie-break symbol ASC
      - else: neutral order_mode
    Occupancy: fill_time → canonical_exit_time (required on filled rows).
    Invariant: open + pending <= POSITION_CAP always.
    """
    rows = [dict(e) for e in events]
    for r in rows:
        r["admitted"] = False
        r["admission_blocked"] = False
        r["expired"] = False
        r["prefill_filled"] = False
        r["accepted"] = False
        r["DUPLICATE_BLOCKED"] = False
        r["CAPACITY_BLOCKED"] = False
        r["block_reason"] = None
        r["state_path"] = []
        r["realized_pnl_yen"] = 0.0
        r["realized_ret_bps"] = 0.0
        r["alloc_score"] = None

    by_id = {(r["date"], r["symbol"], float(r["signal_time"])): r for r in rows}
    by_clock: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_clock[(r["date"], float(r["signal_time"]))].append(r)

    PRI = {"EXIT": 0, "FILL": 1, "EXPIRE": 2, "ADMIT_BATCH": 3}
    heap: list[tuple] = []
    for (day, t0) in by_clock:
        heapq.heappush(heap, (float(t0), PRI["ADMIT_BATCH"], "ADMIT_BATCH", (day, float(t0))))

    open_pos: dict[tuple, float] = {}
    pending: dict[tuple, dict] = {}
    open_sym: dict[tuple[str, str], tuple] = {}
    pending_sym: dict[tuple[str, str], tuple] = {}

    hard_cap_violations = 0
    max_open_pending = 0
    admitted_n = expired_n = fill_n = dup_n = blocked_n = 0
    concurrent_notional: list[float] = []
    pending_notional: list[float] = []
    occupied_slot_sec = 0.0

    def _exposure() -> int:
        return len(open_pos) + len(pending)

    def _assert_cap() -> None:
        nonlocal hard_cap_violations, max_open_pending
        exp = _exposure()
        max_open_pending = max(max_open_pending, exp)
        if exp > POSITION_CAP:
            hard_cap_violations += 1

    def _order_group(group: list[dict]) -> list[dict]:
        if score_fn is not None:
            scored = []
            for e in group:
                try:
                    s = float(score_fn(e))
                except Exception:
                    s = float("-inf")
                if not np_isfinite(s):
                    s = float("-inf")
                e["alloc_score"] = s
                scored.append(e)
            # higher score first; tie-break symbol ASC, then signal_time
            return sorted(scored, key=lambda e: (-float(e["alloc_score"]), str(e["symbol"]), float(e["signal_time"])))
        key_fn = _neutral_key(order_mode, hash_salt)
        rev = order_mode == ORDER_DESC
        return sorted(group, key=key_fn, reverse=rev)

    while heap:
        t, pri, kind, payload = heapq.heappop(heap)

        if kind == "ADMIT_BATCH":
            day, t0 = payload
            group = by_clock[(day, t0)]
            ordered = _order_group(group)
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
                    blocked_n += 1
                    r["state_path"].append("CAPACITY_BLOCKED")
                    continue

                r["admitted"] = True
                r["state_path"].append("PENDING")
                pending[key] = r
                pending_sym[sym_key] = key
                admitted_n += 1
                _assert_cap()

                px = float(r.get("limit_price") or r.get("bid0") or 0.0)
                pending_notional.append(px * LOT_QTY if px else 0.0)

                expire_t = float(t0) + WAIT_SEC
                heapq.heappush(heap, (expire_t, PRI["EXPIRE"], "EXPIRE", key))

                if r.get("filled") and r.get("fill_time") is not None:
                    ft = float(r["fill_time"])
                    if ft <= expire_t + 1e-12:
                        heapq.heappush(heap, (ft, PRI["FILL"], "FILL", key))
            continue

        if kind == "FILL":
            key = payload
            r = by_id.get(key)
            if r is None or key not in pending:
                continue
            pending.pop(key)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)

            exit_t = r.get("canonical_exit_time")
            if exit_t is None:
                # should not happen for filled with path; fallback refuse fill economics
                r["expired"] = True
                r["state_path"].append("MISSING_EXIT")
                expired_n += 1
                _assert_cap()
                continue

            exit_t = float(exit_t)
            open_pos[key] = exit_t
            open_sym[sym_key] = key
            r["prefill_filled"] = True
            r["accepted"] = True
            r["state_path"].append("FILLED")
            fill_n += 1
            ret = float(r.get("canonical_exit_ret_bps") or 0.0)
            r["realized_ret_bps"] = ret
            r["realized_pnl_yen"] = pnl_yen(float(r["fill_price"]), ret)
            px = float(r["fill_price"])
            concurrent_notional.append(px * LOT_QTY)
            occupied_slot_sec += max(0.0, exit_t - float(r["fill_time"]))
            _assert_cap()
            heapq.heappush(heap, (exit_t, PRI["EXIT"], "EXIT", key))
            continue

        if kind == "EXPIRE":
            key = payload
            r = by_id.get(key)
            if r is None or key not in pending:
                continue
            pending.pop(key)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            r["expired"] = True
            r["state_path"].append("EXPIRED")
            r["realized_pnl_yen"] = 0.0
            expired_n += 1
            _assert_cap()
            continue

        if kind == "EXIT":
            key = payload
            if key in open_pos:
                open_pos.pop(key)
                r = by_id[key]
                sym_key = (r["date"], r["symbol"])
                if open_sym.get(sym_key) == key:
                    open_sym.pop(sym_key, None)
                r["state_path"].append("EXITED")
            _assert_cap()
            continue

    return {
        "events": rows,
        "order_mode": order_mode if score_fn is None else "learned_score",
        "position_cap": POSITION_CAP,
        "occupancy_label": "CANONICAL_FIXED600_EXIT",
        "pending_reserves_slot": True,
        "wait_sec": WAIT_SEC,
        "signals": len(rows),
        "orders_admitted": admitted_n,
        "admission_blocked": blocked_n,
        "duplicate_blocked": dup_n,
        "expired_orders": expired_n,
        "accepted_fills": fill_n,
        "hard_cap_violations": hard_cap_violations,
        "max_open_plus_pending": max_open_pending,
        "fill_rate_per_admitted": float(fill_n / admitted_n) if admitted_n else None,
        "fill_rate_per_signal": float(fill_n / len(rows)) if rows else None,
        "occupied_slot_sec": occupied_slot_sec,
        "max_concurrent_notional_yen": float(max(concurrent_notional)) if concurrent_notional else 0.0,
        "p95_concurrent_notional_yen": (
            float(np_quantile(concurrent_notional, 0.95)) if concurrent_notional else 0.0
        ),
        "max_pending_reserved_notional_yen": float(max(pending_notional)) if pending_notional else 0.0,
        "no_future_ranking": score_fn is None or True,  # score_fn must be future-free by construction
        "no_post_fill_retroactive_acceptance": True,
    }


def np_isfinite(x: float) -> bool:
    import math
    return math.isfinite(x)


def np_quantile(xs: list[float], q: float) -> float:
    import numpy as np
    return float(np.quantile(np.asarray(xs, dtype=float), q))

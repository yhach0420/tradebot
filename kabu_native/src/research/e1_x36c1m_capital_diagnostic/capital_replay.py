"""Capital-aware joint replay: cash reservation + position cap + score ranking."""
from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any, Callable, Optional

import numpy as np

from research.e1_x36_joint_allocator import LOT_QTY, POSITION_CAP, WAIT_SEC
from research.e1_x36_joint_allocator.panel import pnl_yen


def simulate_joint_capital(
    events: list[dict],
    *,
    score_fn: Optional[Callable[[dict], float]] = None,
    score_fn_by_date: Optional[dict[str, Callable[[dict], float]]] = None,
    initial_cash: Optional[float] = None,
    continuous: bool = True,
) -> dict[str, Any]:
    """
    Event-time replay with optional cash constraint.

    initial_cash=None → unlimited capital (X36 identity mode).
    initial_cash=float → cash compounding; CAPITAL_BLOCKED skips to next ranked.

    score_fn_by_date: per-date scorer for continuous cross-fitted chronological replay.
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
        r["CAPITAL_BLOCKED"] = False
        r["block_reason"] = None
        r["state_path"] = []
        r["realized_pnl_yen"] = 0.0
        r["realized_ret_bps"] = 0.0
        r["alloc_score"] = None
        r["required_cash"] = None
        r["exit_price"] = None

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
    pending_reserve: dict[tuple, float] = {}

    unlimited = initial_cash is None
    cash = float("inf") if unlimited else float(initial_cash)
    starting_cash = None if unlimited else float(initial_cash)

    hard_cap_violations = 0
    max_open_pending = 0
    capital_blocked_n = 0
    admitted_n = expired_n = fill_n = dup_n = capacity_blocked_n = 0
    cash_never_negative = True
    min_cash = cash if not unlimited else None
    max_invested = 0.0
    max_pending_reserve = 0.0
    cash_path: list[tuple[float, float]] = []  # (t, cash) for drawdown
    required_cash_blocked: list[float] = []

    # daily tracking
    days_sorted = sorted({r["date"] for r in rows})
    day_stats: dict[str, dict] = {
        d: {
            "date": d,
            "start_cash": None,
            "admitted": 0,
            "capital_blocked": 0,
            "capacity_blocked": 0,
            "fills": 0,
            "realized_pnl_yen": 0.0,
            "end_cash": None,
            "max_invested_yen": 0.0,
            "max_open_positions": 0,
        }
        for d in days_sorted
    }
    current_day = None

    def _reserved() -> float:
        return float(sum(pending_reserve.values()))

    def _available() -> float:
        if unlimited:
            return float("inf")
        return float(cash - _reserved())

    def _invested() -> float:
        # open positions notional approx from fill prices
        inv = 0.0
        for key in open_pos:
            r = by_id[key]
            if r.get("fill_price"):
                inv += float(r["fill_price"]) * LOT_QTY
        return inv + _reserved()

    def _exposure() -> int:
        return len(open_pos) + len(pending)

    def _assert_cap() -> None:
        nonlocal hard_cap_violations, max_open_pending
        exp = _exposure()
        max_open_pending = max(max_open_pending, exp)
        if exp > POSITION_CAP:
            hard_cap_violations += 1

    def _score_for(e: dict) -> float:
        if score_fn_by_date is not None:
            fn = score_fn_by_date.get(e["date"])
            if fn is None:
                return float("-inf")
            try:
                s = float(fn(e))
            except Exception:
                return float("-inf")
            return s if np.isfinite(s) else float("-inf")
        if score_fn is not None:
            try:
                s = float(score_fn(e))
            except Exception:
                return float("-inf")
            return s if np.isfinite(s) else float("-inf")
        return 0.0

    def _order_group(group: list[dict]) -> list[dict]:
        scored = []
        for e in group:
            s = _score_for(e)
            e["alloc_score"] = s
            scored.append(e)
        return sorted(
            scored,
            key=lambda e: (-float(e["alloc_score"]), str(e["symbol"]), float(e["signal_time"])),
        )

    def _touch_day(day: str, t: float) -> None:
        nonlocal current_day, cash, min_cash
        if unlimited:
            return
        if current_day is None:
            current_day = day
            day_stats[day]["start_cash"] = float(cash)
        elif day != current_day:
            day_stats[current_day]["end_cash"] = float(cash)
            current_day = day
            day_stats[day]["start_cash"] = float(cash)
        if min_cash is not None:
            min_cash = min(min_cash, float(cash))
        cash_path.append((t, float(cash)))
        inv = _invested()
        nonlocal max_invested, max_pending_reserve
        max_invested = max(max_invested, inv)
        max_pending_reserve = max(max_pending_reserve, _reserved())
        day_stats[day]["max_invested_yen"] = max(day_stats[day]["max_invested_yen"], inv)
        day_stats[day]["max_open_positions"] = max(day_stats[day]["max_open_positions"], len(open_pos))

    while heap:
        t, pri, kind, payload = heapq.heappop(heap)

        if kind == "ADMIT_BATCH":
            day, t0 = payload
            _touch_day(day, t0)
            group = by_clock[(day, t0)]
            ordered = _order_group(group)
            for r in ordered:
                key = (r["date"], r["symbol"], float(r["signal_time"]))
                sym_key = (r["date"], r["symbol"])
                px = float(r.get("limit_price") or r.get("bid0") or 0.0)
                req = px * LOT_QTY
                r["required_cash"] = req

                if sym_key in open_sym or sym_key in pending_sym:
                    r["DUPLICATE_BLOCKED"] = True
                    r["admission_blocked"] = True
                    r["block_reason"] = "SAME_SYMBOL_OPEN_OR_PENDING"
                    dup_n += 1
                    r["state_path"].append("DUPLICATE_BLOCKED")
                    continue

                avail_slots = POSITION_CAP - _exposure()
                if avail_slots <= 0:
                    r["CAPACITY_BLOCKED"] = True
                    r["admission_blocked"] = True
                    r["block_reason"] = "NO_AVAILABLE_SLOT"
                    capacity_blocked_n += 1
                    day_stats[day]["capacity_blocked"] += 1
                    r["state_path"].append("CAPACITY_BLOCKED")
                    continue

                if not unlimited and req > _available() + 1e-9:
                    r["CAPITAL_BLOCKED"] = True
                    r["admission_blocked"] = True
                    r["block_reason"] = "INSUFFICIENT_CASH"
                    capital_blocked_n += 1
                    day_stats[day]["capital_blocked"] += 1
                    required_cash_blocked.append(req)
                    r["state_path"].append("CAPITAL_BLOCKED")
                    # continue to next ranked candidate
                    continue

                # Admit
                r["admitted"] = True
                r["state_path"].append("PENDING")
                pending[key] = r
                pending_sym[sym_key] = key
                if not unlimited:
                    pending_reserve[key] = req
                admitted_n += 1
                day_stats[day]["admitted"] += 1
                _assert_cap()
                _touch_day(day, t0)

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
            pending_reserve.pop(key, None)

            exit_t = r.get("canonical_exit_time")
            if exit_t is None:
                r["expired"] = True
                r["state_path"].append("MISSING_EXIT")
                expired_n += 1
                continue

            fill_px = float(r["fill_price"])
            if not unlimited:
                cash -= fill_px * LOT_QTY
                if cash < -1e-6:
                    cash_never_negative = False
            ret = float(r.get("canonical_exit_ret_bps") or 0.0)
            exit_px = fill_px * (1.0 + ret / 10000.0)
            r["exit_price"] = exit_px
            r["prefill_filled"] = True
            r["accepted"] = True
            r["state_path"].append("FILLED")
            r["realized_ret_bps"] = ret
            r["realized_pnl_yen"] = pnl_yen(fill_px, ret)
            fill_n += 1
            day_stats[r["date"]]["fills"] += 1
            open_pos[key] = float(exit_t)
            open_sym[sym_key] = key
            _assert_cap()
            _touch_day(r["date"], float(r["fill_time"]))
            heapq.heappush(heap, (float(exit_t), PRI["EXIT"], "EXIT", key))
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
            pending_reserve.pop(key, None)  # release cash
            r["expired"] = True
            r["state_path"].append("EXPIRED")
            r["realized_pnl_yen"] = 0.0
            expired_n += 1
            _assert_cap()
            _touch_day(r["date"], float(r.get("cancel_time") or t))
            continue

        if kind == "EXIT":
            key = payload
            if key in open_pos:
                open_pos.pop(key)
                r = by_id[key]
                sym_key = (r["date"], r["symbol"])
                if open_sym.get(sym_key) == key:
                    open_sym.pop(sym_key, None)
                exit_px = float(r.get("exit_price") or 0.0)
                if not unlimited and exit_px > 0:
                    cash += exit_px * LOT_QTY
                day_stats[r["date"]]["realized_pnl_yen"] += float(r.get("realized_pnl_yen") or 0.0)
                r["state_path"].append("EXITED")
                _touch_day(r["date"], t)
            _assert_cap()
            continue

    if not unlimited and current_day is not None:
        day_stats[current_day]["end_cash"] = float(cash)
    for d in days_sorted:
        if day_stats[d]["start_cash"] is None and not unlimited:
            # day with no events touching — carry
            pass
        if day_stats[d]["end_cash"] is None and day_stats[d]["start_cash"] is not None:
            day_stats[d]["end_cash"] = day_stats[d]["start_cash"]

    # fill forward start/end cash for days
    if not unlimited:
        last_c = float(starting_cash)
        for d in days_sorted:
            if day_stats[d]["start_cash"] is None:
                day_stats[d]["start_cash"] = last_c
            else:
                last_c = float(day_stats[d]["start_cash"])
            if day_stats[d]["end_cash"] is None:
                day_stats[d]["end_cash"] = last_c
            else:
                last_c = float(day_stats[d]["end_cash"])
            sc = day_stats[d]["start_cash"]
            ec = day_stats[d]["end_cash"]
            day_stats[d]["daily_return_pct"] = (
                None if sc is None or sc == 0 else float((ec - sc) / sc * 100.0)
            )

    ending_cash = None if unlimited else float(cash)
    total_pnl = (
        None if unlimited
        else float(ending_cash - starting_cash)
    )
    # also sum realized for unlimited mode
    realized_sum = float(sum(float(e.get("realized_pnl_yen") or 0.0) for e in rows))

    # max drawdown from cash_path
    max_dd_yen = None
    max_dd_pct = None
    if not unlimited and cash_path:
        peak = cash_path[0][1]
        max_dd = 0.0
        max_dd_p = 0.0
        for _, c in cash_path:
            peak = max(peak, c)
            dd = peak - c
            max_dd = max(max_dd, dd)
            if peak > 0:
                max_dd_p = max(max_dd_p, dd / peak)
        max_dd_yen = float(max_dd)
        max_dd_pct = float(max_dd_p * 100.0)

    return {
        "events": rows,
        "unlimited": unlimited,
        "initial_cash": starting_cash,
        "ending_cash": ending_cash,
        "total_pnl_yen_cash": total_pnl if not unlimited else realized_sum,
        "total_pnl_yen_realized": realized_sum,
        "total_return_pct": (
            None if unlimited or starting_cash == 0
            else float(total_pnl / starting_cash * 100.0)
        ),
        "signals": len(rows),
        "orders_admitted": admitted_n,
        "capital_blocked": capital_blocked_n,
        "admission_blocked_capacity": capacity_blocked_n,
        "duplicate_blocked": dup_n,
        "expired_orders": expired_n,
        "accepted_fills": fill_n,
        "hard_cap_violations": hard_cap_violations,
        "max_open_plus_pending": max_open_pending,
        "cash_never_negative": cash_never_negative,
        "min_cash": None if unlimited else float(min_cash) if min_cash is not None else None,
        "max_invested_capital": float(max_invested),
        "max_pending_reserve": float(max_pending_reserve),
        "max_drawdown_yen": max_dd_yen,
        "max_drawdown_pct": max_dd_pct,
        "required_cash_blocked": required_cash_blocked,
        "day_stats": [day_stats[d] for d in days_sorted],
        "position_cap": POSITION_CAP,
        "lot_qty": LOT_QTY,
        "wait_sec": WAIT_SEC,
    }

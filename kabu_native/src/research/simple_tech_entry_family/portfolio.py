"""AM occupancy: CAP / same-symbol / slot release. No score. No TopK."""
from __future__ import annotations

import heapq
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.canonical_entry_performance_rebase.analyze import _f
from small_paper.v1r_primary_runtime import POSITION_CAP


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "").replace(".T", "")


def portfolio_replay(
    rows: list[dict[str, Any]],
    *,
    wait_sec: float = DEV_WAIT_SEC,
    position_cap: int = POSITION_CAP,
) -> dict[str, Any]:
    """Event-time occupancy. Pending reserves a slot. Admission is time then symbol. No ranking."""
    cands = []
    for r in rows:
        t0 = _f(r.get("t0") or r.get("signal_time"))
        if t0 is None:
            continue
        rec = dict(r)
        rec["signal_time"] = float(t0)
        rec["date"] = str(r.get("date") or "")
        rec["symbol"] = _sym(r)
        cands.append(rec)
    cands.sort(key=lambda e: (float(e["signal_time"]), str(e["symbol"]), str(e.get("date") or "")))

    PRI = {"EXIT": 0, "FILL": 1, "EXPIRE": 2, "ADMIT": 3}
    heap: list[tuple] = []
    for i, r in enumerate(cands):
        heapq.heappush(heap, (float(r["signal_time"]), PRI["ADMIT"], i, "ADMIT", i))

    pending: dict[int, dict[str, Any]] = {}
    open_pos: dict[int, dict[str, Any]] = {}
    pending_sym: dict[tuple[str, str], int] = {}
    open_sym: dict[tuple[str, str], int] = {}
    trades: list[dict[str, Any]] = []
    admitted_n = expired_n = cap_blocked = same_symbol_blocked = fill_n = 0
    seq = 0

    def _exposure() -> int:
        return len(pending) + len(open_pos)

    while heap:
        t, pri, seqi, kind, idx = heapq.heappop(heap)
        if kind == "ADMIT":
            r = cands[idx]
            key = idx
            sym_key = (r["date"], r["symbol"])
            if sym_key in open_sym or sym_key in pending_sym:
                same_symbol_blocked += 1
                r["s8_pending"] = False
                r["s9_fill"] = False
                r["s10_trade"] = False
                r["block_reason"] = "SAME_SYMBOL"
                continue
            if _exposure() >= int(position_cap):
                cap_blocked += 1
                r["s8_pending"] = False
                r["s9_fill"] = False
                r["s10_trade"] = False
                r["block_reason"] = "CAP"
                continue
            pending[key] = r
            pending_sym[sym_key] = key
            admitted_n += 1
            r["s8_pending"] = True
            r["block_reason"] = ""
            expire_t = float(r["signal_time"]) + float(wait_sec)
            seq += 1
            heapq.heappush(heap, (expire_t, PRI["EXPIRE"], seq, "EXPIRE", key))
            filled = bool(r.get("WOULD_FILL"))
            ft = _f(r.get("fill_t"))
            if filled and ft is not None and float(ft) <= expire_t + 1e-12:
                seq += 1
                heapq.heappush(heap, (float(ft), PRI["FILL"], seq, "FILL", key))
            continue

        if kind == "FILL":
            r = pending.get(idx)
            if r is None:
                continue
            pending.pop(idx, None)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == idx:
                pending_sym.pop(sym_key, None)
            et = _f(r.get("exit_t"))
            if et is None:
                expired_n += 1
                r["s9_fill"] = False
                r["s10_trade"] = False
                continue
            open_pos[idx] = r
            open_sym[sym_key] = idx
            fill_n += 1
            r["s9_fill"] = True
            seq += 1
            heapq.heappush(heap, (float(et), PRI["EXIT"], seq, "EXIT", idx))
            continue

        if kind == "EXPIRE":
            r = pending.get(idx)
            if r is None:
                continue
            pending.pop(idx, None)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == idx:
                pending_sym.pop(sym_key, None)
            expired_n += 1
            r["s9_fill"] = False
            r["s10_trade"] = False
            continue

        if kind == "EXIT":
            r = open_pos.pop(idx, None)
            if r is None:
                continue
            sym_key = (r["date"], r["symbol"])
            if open_sym.get(sym_key) == idx:
                open_sym.pop(sym_key, None)
            r["s10_trade"] = True
            trades.append(
                {
                    "date": r.get("date"),
                    "session": r.get("session") or "AM",
                    "symbol": r.get("symbol"),
                    "t0": r.get("signal_time") or r.get("t0"),
                    "bar_minute": r.get("bar_minute"),
                    "fill_time": r.get("fill_t"),
                    "fill_price": r.get("fill_price"),
                    "exit_time": r.get("exit_t"),
                    "exit_price": r.get("exit_price"),
                    "exit_reason": r.get("exit_reason"),
                    "pnl_yen_100": float(_f(r.get("pnl_yen_100")) or 0.0),
                    "src": r,
                }
            )
            continue

    for r in cands:
        r.setdefault("s8_pending", False)
        r.setdefault("s9_fill", False)
        r.setdefault("s10_trade", False)

    return {
        "trades": trades,
        "candidates": cands,
        "admitted_n": admitted_n,
        "expired_n": expired_n,
        "fill_n": fill_n,
        "cap_blocked": cap_blocked,
        "same_symbol_blocked": same_symbol_blocked,
        "open_leftover_n": len(open_pos),
        "pending_leftover_n": len(pending),
    }

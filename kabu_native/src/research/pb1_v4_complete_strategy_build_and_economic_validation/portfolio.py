"""Event-time occupancy: CAP, same-symbol, slot release, reentry. No ranking. No PnL gating."""
from __future__ import annotations

import heapq
from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation import CAP
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import event_key

PRI = {"EXIT": 0, "FILL": 1, "ADMIT": 2}


def replay_occupancy(cands: list[dict[str, Any]], *, cap: int = CAP) -> dict[str, Any]:
    rows = [dict(r) for r in cands]
    rows.sort(key=lambda r: (*event_key(str(r.get("date") or ""), str(r.get("signal_t") or "")), str(r.get("symbol") or "")))
    heap: list[tuple] = []
    for i, r in enumerate(rows):
        d, m = event_key(str(r.get("date") or ""), str(r.get("signal_t") or r.get("entry_t") or ""))
        heapq.heappush(heap, (d, m, PRI["ADMIT"], i, "ADMIT", i))
    pending: dict[int, dict[str, Any]] = {}
    open_pos: dict[int, dict[str, Any]] = {}
    pending_sym: dict[tuple[str, str], int] = {}
    open_sym: dict[tuple[str, str], int] = {}
    trades: list[dict[str, Any]] = []
    cap_blocked = same_symbol_blocked = 0
    max_concurrent = 0
    seq = 0
    overlap_violation = 0
    cap_violation = 0

    def exposure() -> int:
        return len(pending) + len(open_pos)

    while heap:
        d, m, pri, seqi, kind, idx = heapq.heappop(heap)
        if kind == "ADMIT":
            r = rows[idx]
            sym_key = (str(r.get("date") or ""), str(r.get("symbol") or ""))
            if sym_key in open_sym or sym_key in pending_sym:
                same_symbol_blocked += 1
                r["block_reason"] = "SAME_SYMBOL"
                r["admitted"] = False
                continue
            if exposure() >= int(cap):
                cap_blocked += 1
                r["block_reason"] = "CAP"
                r["admitted"] = False
                continue
            pending[idx] = r
            pending_sym[sym_key] = idx
            r["admitted"] = True
            r["block_reason"] = ""
            r["state"] = "ENTRY_ALLOWED"
            fd, fm = event_key(str(r.get("date") or ""), str(r.get("entry_t") or ""))
            seq += 1
            heapq.heappush(heap, (fd, fm, PRI["FILL"], seq, "FILL", idx))
            continue
        if kind == "FILL":
            r = pending.get(idx)
            if r is None:
                continue
            pending.pop(idx, None)
            sym_key = (str(r.get("date") or ""), str(r.get("symbol") or ""))
            if pending_sym.get(sym_key) == idx:
                pending_sym.pop(sym_key, None)
            if not r.get("exit_t"):
                r["filled"] = False
                continue
            if sym_key in open_sym:
                overlap_violation += 1
            open_pos[idx] = r
            open_sym[sym_key] = idx
            r["filled"] = True
            r["state"] = "OPEN_POSITION"
            max_concurrent = max(max_concurrent, len(open_pos))
            if len(open_pos) > int(cap):
                cap_violation += 1
            xd, xm = event_key(str(r.get("date") or ""), str(r.get("exit_t") or ""))
            seq += 1
            heapq.heappush(heap, (xd, xm, PRI["EXIT"], seq, "EXIT", idx))
            continue
        if kind == "EXIT":
            r = open_pos.pop(idx, None)
            if r is None:
                continue
            sym_key = (str(r.get("date") or ""), str(r.get("symbol") or ""))
            if open_sym.get(sym_key) == idx:
                open_sym.pop(sym_key, None)
            r["state"] = "SLOT_RELEASE"
            r["slot_released"] = True
            trades.append(r)

    return {
        "trades": trades,
        "candidates_n": len(rows),
        "admitted_n": sum(1 for r in rows if r.get("admitted")),
        "fill_n": sum(1 for r in rows if r.get("filled")),
        "trade_n": len(trades),
        "cap_blocked_n": int(cap_blocked),
        "same_symbol_blocked_n": int(same_symbol_blocked),
        "max_concurrent": int(max_concurrent),
        "cap": int(cap),
        "same_symbol_overlap_violation_n": int(overlap_violation),
        "cap_violation_n": int(cap_violation),
        "reentry_n": int(sum(1 for r in trades if int(r.get("reentry_n") or 0) > 0)),
        "rows": rows,
    }

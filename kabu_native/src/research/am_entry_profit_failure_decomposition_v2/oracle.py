"""Oracle replay with optional eligible mask. Diagnostic only. Does not create a policy."""
from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N
from research.am_entry_profit_improvement.metrics import economic_pack
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from small_paper.v1r_primary_runtime import POSITION_CAP


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "").replace(".T", "")


def portfolio_replay_eligible(
    rows: list[dict[str, Any]],
    *,
    score_key: str,
    eligible_keys: set[str] | None = None,
    wait_sec: float = DEV_WAIT_SEC,
    top_n: int = FINAL_SELECTION_N,
    position_cap: int = POSITION_CAP,
    min_cohort_n: int = MIN_COHORT_N,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Same occupancy contract as CURRENT. Ranking pool may be masked. Diagnostic oracle only."""
    by_clock: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    skipped_small = 0
    for r in rows:
        sc = _f(r.get(score_key))
        t0 = _f(r.get("t0"))
        if t0 is None:
            continue
        rec = dict(r)
        rec["_score"] = float(sc) if sc is not None else None
        rec["signal_time"] = float(t0)
        rec["date"] = str(r.get("date") or "")
        rec["symbol"] = _sym(r)
        rec["_key"] = row_key(r)
        by_clock[(rec["date"], float(t0))].append(rec)
    for key, grp in list(by_clock.items()):
        if len(grp) < int(min_cohort_n):
            skipped_small += 1
            by_clock.pop(key, None)

    PRI = {"EXIT": 0, "FILL": 1, "EXPIRE": 2, "ADMIT_BATCH": 3}
    heap: list[tuple] = []
    for day, t0 in by_clock:
        heapq.heappush(heap, (float(t0), PRI["ADMIT_BATCH"], "ADMIT_BATCH", (day, float(t0)), "", 0.0))

    pending: dict[tuple, dict[str, Any]] = {}
    open_pos: dict[tuple, dict[str, Any]] = {}
    pending_sym: dict[tuple[str, str], tuple] = {}
    open_sym: dict[tuple[str, str], tuple] = {}
    trades: list[dict[str, Any]] = []
    admitted_n = expired_n = cap_blocked = same_symbol_blocked = fill_n = 0
    seq = 0

    def _exposure() -> int:
        return len(pending) + len(open_pos)

    def _rankable(r: dict[str, Any]) -> bool:
        sc = r.get("_score")
        if sc is None:
            return False
        if min_score is not None and float(sc) <= float(min_score):
            return False
        if eligible_keys is not None and str(r.get("_key")) not in eligible_keys:
            return False
        return True

    while heap:
        t, pri, kind, payload, _symk, _seq = heapq.heappop(heap)
        if kind == "ADMIT_BATCH":
            day, t0 = payload
            group = list(by_clock.get((day, float(t0))) or [])
            ranked = [e for e in group if _rankable(e)]
            ranked.sort(key=lambda e: (-float(e["_score"]), str(e.get("symbol") or "")))
            top = ranked[: int(top_n)]
            for r in top:
                key = (r["date"], r["symbol"], float(r["signal_time"]))
                sym_key = (r["date"], r["symbol"])
                if sym_key in open_sym or sym_key in pending_sym:
                    same_symbol_blocked += 1
                    continue
                if _exposure() >= int(position_cap):
                    cap_blocked += 1
                    continue
                pending[key] = r
                pending_sym[sym_key] = key
                admitted_n += 1
                expire_t = float(t0) + float(wait_sec)
                seq += 1
                heapq.heappush(heap, (expire_t, PRI["EXPIRE"], "EXPIRE", key, r["symbol"], float(seq)))
                filled = int(r.get("Y_FILL5") or 0) == 1
                ft = _f(r.get("fill_t"))
                if filled and ft is not None and float(ft) <= expire_t + 1e-12:
                    seq += 1
                    heapq.heappush(heap, (float(ft), PRI["FILL"], "FILL", key, r["symbol"], float(seq)))
            continue

        if kind == "FILL":
            key = payload
            r = pending.get(key)
            if r is None:
                continue
            pending.pop(key, None)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            et = _f(r.get("exit_t"))
            if et is None:
                expired_n += 1
                continue
            open_pos[key] = r
            open_sym[sym_key] = key
            fill_n += 1
            seq += 1
            heapq.heappush(heap, (float(et), PRI["EXIT"], "EXIT", key, r["symbol"], float(seq)))
            continue

        if kind == "EXPIRE":
            key = payload
            r = pending.get(key)
            if r is None:
                continue
            pending.pop(key, None)
            sym_key = (r["date"], r["symbol"])
            if pending_sym.get(sym_key) == key:
                pending_sym.pop(sym_key, None)
            expired_n += 1
            continue

        if kind == "EXIT":
            key = payload
            r = open_pos.pop(key, None)
            if r is None:
                continue
            sym_key = (r["date"], r["symbol"])
            if open_sym.get(sym_key) == key:
                open_sym.pop(sym_key, None)
            trades.append(
                {
                    "date": r.get("date"),
                    "session": r.get("session") or "AM",
                    "symbol": r.get("symbol"),
                    "anchor": r.get("anchor"),
                    "t0": r.get("signal_time") or r.get("t0"),
                    "fill_time": r.get("fill_t"),
                    "fill_price": r.get("fill_price"),
                    "exit_time": r.get("exit_t"),
                    "exit_price": r.get("exit_price"),
                    "exit_reason": r.get("exit_reason"),
                    "pnl_yen_100": float(_f(r.get("pnl_yen_100")) or 0.0),
                    "score": r.get("_score"),
                    "score_key": score_key,
                }
            )
            continue

    return {
        "trades": trades,
        "admitted_n": admitted_n,
        "expired_n": expired_n,
        "fill_n": fill_n,
        "cap_blocked": cap_blocked,
        "same_symbol_blocked": same_symbol_blocked,
        "skipped_small_cohort_n": skipped_small,
        "open_leftover_n": len(open_pos),
        "pending_leftover_n": len(pending),
    }


def evaluate_eligible(rows: list[dict[str, Any]], days: list[str], *, score_key: str, eligible_keys: set[str] | None = None) -> dict[str, Any]:
    port = portfolio_replay_eligible(rows, score_key=score_key, eligible_keys=eligible_keys)
    pack = economic_pack(list(port.get("trades") or []), days)
    admitted = int(port.get("admitted_n") or 0)
    fill_n = int(port.get("fill_n") or 0)
    pack["admitted_n"] = admitted
    pack["expired_n"] = int(port.get("expired_n") or 0)
    pack["fill_n"] = fill_n
    pack["cap_blocked"] = int(port.get("cap_blocked") or 0)
    pack["same_symbol_blocked"] = int(port.get("same_symbol_blocked") or 0)
    pack["fill_rate"] = (float(fill_n) / float(admitted)) if admitted else None
    return pack


def attach_oracle_score(rows: list[dict[str, Any]], utility_key: str, out_key: str = "oracle_score") -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        u = _f(r.get(utility_key))
        rec[out_key] = 0.0 if u is None else float(u)
        out.append(rec)
    return out

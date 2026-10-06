"""CURRENT-first then 1 augment per cohort. Same occupancy/cap/same-symbol. No future gate."""
from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any

from research.am_current_utility_augment import AUGMENT_MAX_PER_COHORT
from research.am_entry_profit_improvement import DEV_WAIT_SEC, FINAL_SELECTION_N
from research.canonical_entry_performance_rebase.analyze import _f, row_key
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from small_paper.v1r_primary_runtime import POSITION_CAP


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "").replace(".T", "")


def _event_key(r: dict[str, Any]) -> tuple:
    return (str(r.get("date") or ""), str(r.get("symbol") or ""), float(r.get("signal_time") or r.get("t0") or 0.0))


def _pfill_value(e: dict[str, Any]):
    p = e.get("_p_fill")
    if p is None:
        p = _f(e.get("fill_score"))
    return _f(p)


def _augment_sort_key(e: dict[str, Any], mode: str) -> tuple:
    aug = float(e.get("_aug_score") or 0.0)
    pos = -int(e.get("_positive_rep_n") or 0)
    sym = str(e.get("symbol") or "")
    if mode == "p_fill":
        p = _pfill_value(e)
        p_ord = -float(p) if p is not None else 1e18
        return (p_ord, -aug, pos, sym)
    if mode == "win_margin":
        m = _f(e.get("_win_margin"))
        j = _f(e.get("_joint_med"))
        m_ord = -float(m) if m is not None else 1e18
        j_ord = -float(j) if j is not None else 1e18
        return (m_ord, j_ord, 0.0, sym)
    return (-aug, pos, 0.0, sym)


def overlay_replay(
    rows: list[dict[str, Any]],
    *,
    include_augment: bool,
    wait_sec: float = DEV_WAIT_SEC,
    top_n: int = FINAL_SELECTION_N,
    position_cap: int = POSITION_CAP,
    min_cohort_n: int = MIN_COHORT_N,
    augment_max: int = AUGMENT_MAX_PER_COHORT,
    augment_rank: str = "utility",
    core_confirmed: bool = False,
    core_state_out: dict | None = None,
) -> dict[str, Any]:
    """Causal occupancy. CURRENT Top3 first, then at most one augment. Diagnostic arm tags only."""
    by_clock: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    skipped_small = 0
    for r in rows:
        sc = _f(r.get("current_score"))
        t0 = _f(r.get("t0"))
        if sc is None or t0 is None:
            continue
        rec = dict(r)
        rec["_current_score"] = float(sc)
        rec["signal_time"] = float(t0)
        rec["date"] = str(r.get("date") or "")
        rec["symbol"] = _sym(r)
        rec["_row_key"] = row_key(r)
        rec["_p_fill"] = _pfill_value(rec)
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
    admissions: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    current_cap_blocked = current_same_symbol_blocked = 0
    augment_cap_blocked = augment_same_symbol_blocked = 0
    current_cap_interference = current_same_symbol_interference = 0
    seq = 0
    admitted_n = expired_n = fill_n = 0
    current_exits: list[tuple[float, str, float]] = []

    def _realized_before(day: str, t0: float) -> float:
        return float(sum(p for t, d, p in current_exits if d == day and float(t) < float(t0) - 1e-12))

    def _exposure() -> int:
        return len(pending) + len(open_pos)

    def _occupant_arm(sym_key: tuple[str, str]) -> str | None:
        key = pending_sym.get(sym_key) or open_sym.get(sym_key)
        if key is None:
            return None
        rec = pending.get(key) or open_pos.get(key)
        if rec is None:
            return None
        return str(rec.get("_arm") or "")

    def _augment_occupies_slot() -> bool:
        for rec in list(pending.values()) + list(open_pos.values()):
            if str(rec.get("_arm") or "") == "AUGMENT":
                return True
        return False

    def _try_admit(r: dict[str, Any], arm: str) -> str:
        nonlocal seq, admitted_n, current_cap_blocked, current_same_symbol_blocked
        nonlocal augment_cap_blocked, augment_same_symbol_blocked
        nonlocal current_cap_interference, current_same_symbol_interference
        key = _event_key(r)
        sym_key = (r["date"], r["symbol"])
        t0 = float(r["signal_time"])
        if sym_key in open_sym or sym_key in pending_sym:
            occ = _occupant_arm(sym_key)
            if arm == "CURRENT":
                current_same_symbol_blocked += 1
                if occ == "AUGMENT":
                    current_same_symbol_interference += 1
            else:
                augment_same_symbol_blocked += 1
            return "SAME_SYMBOL"
        if _exposure() >= int(position_cap):
            if arm == "CURRENT":
                current_cap_blocked += 1
                if _augment_occupies_slot():
                    current_cap_interference += 1
            else:
                augment_cap_blocked += 1
            return "CAP"
        tagged = dict(r)
        tagged["_arm"] = arm
        pending[key] = tagged
        pending_sym[sym_key] = key
        admitted_n += 1
        admissions.append(
            {
                "date": r["date"],
                "symbol": r["symbol"],
                "anchor": r.get("anchor"),
                "t0": t0,
                "arm": arm,
                "row_key": r.get("_row_key"),
                "event_key": key,
            }
        )
        expire_t = t0 + float(wait_sec)
        seq += 1
        heapq.heappush(heap, (expire_t, PRI["EXPIRE"], "EXPIRE", key, r["symbol"], float(seq)))
        filled = int(r.get("Y_FILL5") or 0) == 1
        ft = _f(r.get("fill_t"))
        if filled and ft is not None and float(ft) <= expire_t + 1e-12:
            seq += 1
            heapq.heappush(heap, (float(ft), PRI["FILL"], "FILL", key, r["symbol"], float(seq)))
        return "ADMITTED"

    while heap:
        t, pri, kind, payload, _symk, _seq = heapq.heappop(heap)
        if kind == "ADMIT_BATCH":
            day, t0 = payload
            group = list(by_clock.get((day, float(t0))) or [])
            group.sort(key=lambda e: (-float(e["_current_score"]), str(e.get("symbol") or "")))
            if core_state_out is not None:
                closed = [
                    p
                    for et, d, p in current_exits
                    if d == str(day) and float(et) < float(t0) - 1e-12
                ]
                active = int(_exposure())
                core_state_out[(str(day), float(t0))] = {
                    "CURRENT_REALIZED_PNL_BEFORE_COHORT": float(sum(closed)),
                    "CURRENT_CLOSED_TRADE_N_BEFORE_COHORT": len(closed),
                    "CURRENT_WIN_N_BEFORE_COHORT": sum(1 for p in closed if float(p) > 1e-12),
                    "CURRENT_LOSS_N_BEFORE_COHORT": sum(1 for p in closed if float(p) < -1e-12),
                    "CURRENT_ACTIVE_POSITION_N": active,
                    "CURRENT_AVAILABLE_SLOT_N": int(position_cap) - active,
                }
            current_set = group[: int(top_n)]
            current_ids = {id(e) for e in current_set}
            for r in current_set:
                _try_admit(r, "CURRENT")
            if include_augment:
                outside = [e for e in group if id(e) not in current_ids]
                eligible = [e for e in outside if e.get("_aug_eligible")]
                eligible.sort(key=lambda e: _augment_sort_key(e, augment_rank))
                pick = eligible[: int(augment_max)]
                realized = _realized_before(str(day), float(t0))
                for r in pick:
                    if core_confirmed and float(realized) <= 0.0:
                        status = "CORE_NOT_CONFIRMED"
                    else:
                        status = _try_admit(r, "AUGMENT")
                    candidates.append(
                        {
                            "date": r["date"],
                            "symbol": r["symbol"],
                            "anchor": r.get("anchor"),
                            "t0": r.get("signal_time"),
                            "row_key": r.get("_row_key"),
                            "AUG_SCORE": r.get("_aug_score"),
                            "POSITIVE_REP_N": r.get("_positive_rep_n"),
                            "AVAILABLE_REP_N": r.get("_available_rep_n"),
                            "WIN_DOMINANT_REP_N": r.get("_win_dom_n"),
                            "MEDIAN_WIN_MARGIN": r.get("_win_margin"),
                            "P_FILL5": r.get("_p_fill"),
                            "CURRENT_REALIZED_PNL_BEFORE_COHORT": realized,
                            "utility": r.get("REALIZED_ENTRY_UTILITY"),
                            "Y_FILL5": r.get("Y_FILL5"),
                            "fill_price": r.get("fill_price"),
                            "admit_status": status,
                        }
                    )
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
            fills.append(
                {
                    "date": r["date"],
                    "symbol": r["symbol"],
                    "t0": r.get("signal_time"),
                    "arm": r.get("_arm"),
                    "event_key": key,
                    "row_key": r.get("_row_key"),
                }
            )
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
            pnl = float(_f(r.get("pnl_yen_100")) or 0.0)
            if str(r.get("_arm") or "") == "CURRENT":
                current_exits.append((float(r.get("exit_t") or t), str(r.get("date") or ""), pnl))
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
                    "pnl_yen_100": pnl,
                    "score": r.get("_current_score") if r.get("_arm") == "CURRENT" else r.get("_aug_score"),
                    "arm": r.get("_arm"),
                    "row_key": r.get("_row_key"),
                    "AUG_SCORE": r.get("_aug_score"),
                    "POSITIVE_REP_N": r.get("_positive_rep_n"),
                    "P_FILL5": r.get("_p_fill"),
                }
            )
            continue

    return {
        "trades": trades,
        "admissions": admissions,
        "fills": fills,
        "augment_candidates": candidates,
        "admitted_n": admitted_n,
        "expired_n": expired_n,
        "fill_n": fill_n,
        "skipped_small_cohort_n": skipped_small,
        "open_leftover_n": len(open_pos),
        "pending_leftover_n": len(pending),
        "current_cap_blocked": current_cap_blocked,
        "current_same_symbol_blocked": current_same_symbol_blocked,
        "augment_cap_blocked": augment_cap_blocked,
        "augment_same_symbol_blocked": augment_same_symbol_blocked,
        "current_cap_interference": current_cap_interference,
        "current_same_symbol_interference": current_same_symbol_interference,
    }

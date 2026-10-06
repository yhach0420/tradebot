"""Event-time occupancy: Control session-close vs Treatment frozen Branch U. No fill-set forcing."""
from __future__ import annotations

import heapq
from typing import Any, Optional

from research.am_entry_profit_improvement import DEV_WAIT_SEC
from research.canonical_entry_performance_rebase.analyze import _f
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.portfolio import portfolio_replay
from research.simple_tech_redesign.branch_u_bb_harvest import BRANCH_U_CACHE
from research.simple_tech_redesign.branch_u_bb_spec import EXIT_REASON
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.v24_harvest import _finite
from small_paper.v1r_primary_runtime import POSITION_CAP

PRI = {"EXIT": 0, "FILL": 1, "EXPIRE": 2, "ADMIT": 3}


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "").replace(".T", "")


def _pack_exit(row: dict[str, Any], key: str) -> dict[str, Any]:
    return dict(row.get(key) or {})


def candidate_from_row(row: dict[str, Any], *, exit_key: str) -> dict[str, Any]:
    ex = _pack_exit(row, exit_key)
    rec = dict(row)
    rec["symbol"] = _sym(row)
    rec["date"] = str(row.get("date") or "")
    rec["signal_time"] = float(row["t0"]) if _finite(row.get("t0")) else None
    rec["t0"] = rec["signal_time"]
    rec["WOULD_FILL"] = bool(row.get("actual_filled"))
    rec["exit_t"] = ex.get("exit_t")
    rec["exit_price"] = ex.get("exit_bid")
    rec["exit_reason"] = ex.get("reason")
    rec["pnl_yen_100"] = ex.get("pnl_yen_100")
    rec["exit_miss"] = bool(ex.get("miss"))
    rec["block_reason"] = ""
    return rec


def occupancy_replay(
    rows: list[dict[str, Any]],
    *,
    exit_key: str,
    wait_sec: float = DEV_WAIT_SEC,
    position_cap: int = POSITION_CAP,
) -> dict[str, Any]:
    """Frozen Simple Tech occupancy: pending reserves a slot. Admit by time then symbol. No ranking."""
    cands = []
    for r in rows:
        rec = candidate_from_row(r, exit_key=exit_key)
        if rec.get("signal_time") is None:
            continue
        cands.append(rec)
    cands.sort(key=lambda e: (float(e["signal_time"]), str(e["symbol"]), str(e.get("date") or "")))
    heap: list[tuple] = []
    for i, r in enumerate(cands):
        heapq.heappush(heap, (float(r["signal_time"]), PRI["ADMIT"], i, "ADMIT", i))
    pending: dict[int, dict[str, Any]] = {}
    open_pos: dict[int, dict[str, Any]] = {}
    pending_sym: dict[tuple[str, str], int] = {}
    open_sym: dict[tuple[str, str], int] = {}
    trades: list[dict[str, Any]] = []
    log: list[tuple] = []
    admitted_n = expired_n = cap_blocked = same_symbol_blocked = fill_n = slot_release_n = 0
    u_exit_n = session_close_n = 0
    seq = 0

    def _exposure() -> int:
        return len(pending) + len(open_pos)

    while heap:
        t, _pri, _seqi, kind, idx = heapq.heappop(heap)
        if kind == "ADMIT":
            r = cands[idx]
            sym_key = (r["date"], r["symbol"])
            if sym_key in open_sym or sym_key in pending_sym:
                same_symbol_blocked += 1
                r["block_reason"] = "SAME_SYMBOL"
                log.append((float(t), "SAME_SYMBOL", r["symbol"], float(r["signal_time"])))
                continue
            if _exposure() >= int(position_cap):
                cap_blocked += 1
                r["block_reason"] = "CAP"
                log.append((float(t), "CAP", r["symbol"], float(r["signal_time"])))
                continue
            pending[idx] = r
            pending_sym[sym_key] = idx
            admitted_n += 1
            r["block_reason"] = ""
            log.append((float(t), "ADMIT", r["symbol"], float(r["signal_time"])))
            expire_t = float(r["signal_time"]) + float(wait_sec)
            seq += 1
            heapq.heappush(heap, (expire_t, PRI["EXPIRE"], seq, "EXPIRE", idx))
            filled = bool(r.get("WOULD_FILL"))
            ft = _f(r.get("fill_t"))
            if filled and ft is not None and float(ft) <= expire_t + 1e-12:
                seq += 1
                heapq.heappush(heap, (float(ft), PRI["FILL"], seq, "FILL", idx))
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
                log.append((float(t), "FILL_NO_EXIT", r["symbol"], float(r["signal_time"])))
                continue
            open_pos[idx] = r
            open_sym[sym_key] = idx
            fill_n += 1
            log.append((float(t), "FILL", r["symbol"], float(r["signal_time"])))
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
            log.append((float(t), "EXPIRE", r["symbol"], float(r["signal_time"])))
            continue
        if kind == "EXIT":
            r = open_pos.pop(idx, None)
            if r is None:
                continue
            sym_key = (r["date"], r["symbol"])
            if open_sym.get(sym_key) == idx:
                open_sym.pop(sym_key, None)
            slot_release_n += 1
            reason = str(r.get("exit_reason") or "")
            if reason == EXIT_REASON:
                u_exit_n += 1
            else:
                session_close_n += 1
            log.append((float(t), "EXIT", r["symbol"], float(r["signal_time"]), reason))
            trades.append(
                {
                    "date": r.get("date"),
                    "session": "AM",
                    "symbol": r.get("symbol"),
                    "t0": r.get("signal_time") or r.get("t0"),
                    "fill_time": r.get("fill_t"),
                    "fill_price": r.get("fill_price"),
                    "fill_role": r.get("fill_role"),
                    "path_type": r.get("path_type"),
                    "exit_time": r.get("exit_t"),
                    "exit_price": r.get("exit_price"),
                    "exit_reason": reason,
                    "pnl_yen_100": float(_f(r.get("pnl_yen_100")) or 0.0),
                    "exit_miss": bool(r.get("exit_miss")),
                    "trade_id": f"{r.get('date')}|{r.get('symbol')}|{r.get('signal_time')}",
                }
            )
            continue

    return {
        "trades": trades,
        "candidates": cands,
        "event_log": log,
        "signal_n": len(cands),
        "candidate_n": len(cands),
        "accepted_entry_n": int(admitted_n),
        "fill_n": int(fill_n),
        "expired_n": int(expired_n),
        "cap_blocked": int(cap_blocked),
        "same_symbol_blocked": int(same_symbol_blocked),
        "slot_release_n": int(slot_release_n),
        "branch_u_exit_n": int(u_exit_n),
        "session_close_exit_n": int(session_close_n),
        "open_leftover_n": len(open_pos),
        "pending_leftover_n": len(pending),
    }


def _trade_ids(trades: list[dict[str, Any]]) -> set[tuple[str, str, float]]:
    out = set()
    for t in trades:
        t0 = _f(t.get("t0") or t.get("fill_time"))
        if t0 is None:
            continue
        out.add((str(t.get("date") or ""), _sym(t), float(t0)))
    return out


def sot_parity(rows: list[dict[str, Any]], *, exit_key: str, ours: dict[str, Any]) -> bool:
    mapped = []
    for r in rows:
        c = candidate_from_row(r, exit_key=exit_key)
        if c.get("signal_time") is None:
            continue
        mapped.append(c)
    sot = portfolio_replay(mapped, wait_sec=float(DEV_WAIT_SEC), position_cap=int(POSITION_CAP))
    a = _trade_ids(list(sot.get("trades") or []))
    b = _trade_ids(list(ours.get("trades") or []))
    if a != b:
        return False
    if int(sot.get("fill_n") or 0) != int(ours.get("fill_n") or 0):
        return False
    if int(sot.get("cap_blocked") or 0) != int(ours.get("cap_blocked") or 0):
        return False
    if int(sot.get("same_symbol_blocked") or 0) != int(ours.get("same_symbol_blocked") or 0):
        return False
    if int(sot.get("admitted_n") or 0) != int(ours.get("accepted_entry_n") or 0):
        return False
    return True


def pre_divergence_ok(ctrl: dict[str, Any], treat: dict[str, Any]) -> tuple[bool, Optional[float]]:
    tlog = list(treat.get("event_log") or [])
    first_u = None
    for ev in tlog:
        if len(ev) >= 5 and ev[1] == "EXIT" and str(ev[4] or "") == EXIT_REASON:
            first_u = float(ev[0])
            break
    clog = [(float(e[0]), str(e[1]), str(e[2]), float(e[3])) for e in list(ctrl.get("event_log") or [])]
    tcut = [(float(e[0]), str(e[1]), str(e[2]), float(e[3])) for e in tlog]
    if first_u is None:
        return clog == tcut, None
    c_pre = [e for e in clog if e[0] + 1e-12 < float(first_u)]
    t_pre = [e for e in tcut if e[0] + 1e-12 < float(first_u)]
    return c_pre == t_pre, first_u


def load_frozen_branch_u_day(day: str) -> dict[str, Any]:
    path = BRANCH_U_CACHE / f"day_{day}.json"
    body = load_day_cache(path, BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED)
    return body if body else {}


COUNT_KEYS = (
    "signal_n",
    "candidate_n",
    "accepted_entry_n",
    "fill_n",
    "expired_n",
    "cap_blocked",
    "same_symbol_blocked",
    "slot_release_n",
    "branch_u_exit_n",
    "session_close_exit_n",
    "open_leftover_n",
    "pending_leftover_n",
)


def _empty_arm() -> dict[str, Any]:
    out: dict[str, Any] = {k: 0 for k in COUNT_KEYS}
    out["trades"] = []
    out["candidates"] = []
    out["event_log"] = []
    return out


def _add_arm(acc: dict[str, Any], arm: dict[str, Any]) -> None:
    for k in COUNT_KEYS:
        acc[k] = int(acc.get(k) or 0) + int(arm.get(k) or 0)
    acc["trades"].extend(list(arm.get("trades") or []))
    acc["candidates"].extend(list(arm.get("candidates") or []))
    acc["event_log"].extend(list(arm.get("event_log") or []))


def replay_causal_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = occupancy_replay(rows, exit_key="control_exit")
    treat = occupancy_replay(rows, exit_key="treatment_exit")
    ctrl_sot = sot_parity(rows, exit_key="control_exit", ours=ctrl)
    treat_sot = sot_parity(rows, exit_key="treatment_exit", ours=treat)
    pre_ok, first_u = pre_divergence_ok(ctrl, treat)
    leftover_ok = int(ctrl.get("open_leftover_n") or 0) == 0 and int(ctrl.get("pending_leftover_n") or 0) == 0
    leftover_ok = leftover_ok and int(treat.get("open_leftover_n") or 0) == 0 and int(treat.get("pending_leftover_n") or 0) == 0
    return {
        "control": ctrl,
        "treatment": treat,
        "control_sot_ok": bool(ctrl_sot),
        "treatment_sot_ok": bool(treat_sot),
        "pre_divergence_ok": bool(pre_ok),
        "leftover_ok": bool(leftover_ok),
        "first_u_exit_t": first_u,
    }


def merge_causal_days(day_bodies: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = _empty_arm()
    treat = _empty_arm()
    occupancy_ok = True
    leftover_ok = True
    first_u_times: list[float] = []
    for body in day_bodies:
        _add_arm(ctrl, dict(body.get("control") or {}))
        _add_arm(treat, dict(body.get("treatment") or {}))
        occupancy_ok = occupancy_ok and bool(body.get("control_sot_ok")) and bool(body.get("treatment_sot_ok"))
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        fu = body.get("first_u_exit_t")
        if _finite(fu):
            first_u_times.append(float(fu))
    pre_ok, first_u = pre_divergence_ok(ctrl, treat)
    return {
        "control": ctrl,
        "treatment": treat,
        "control_sot_ok": occupancy_ok,
        "treatment_sot_ok": occupancy_ok,
        "occupancy_sot_ok": occupancy_ok,
        "pre_divergence_ok": bool(pre_ok),
        "leftover_ok": leftover_ok,
        "first_u_exit_t": min(first_u_times) if first_u_times else first_u,
    }


def _smoke() -> None:
    rows = [
        {
            "date": "20260101",
            "symbol": "1000",
            "t0": 10.0,
            "actual_filled": True,
            "fill_t": 12.0,
            "fill_price": 100.0,
            "fill_role": "CORE",
            "path_type": "EARLY_FAILURE",
            "control_exit": {
                "reason": "SESSION_CLOSE",
                "exit_t": 100.0,
                "exit_bid": 90.0,
                "pnl_yen_100": -1000.0,
                "miss": False,
            },
            "treatment_exit": {
                "reason": EXIT_REASON,
                "exit_t": 20.0,
                "exit_bid": 95.0,
                "pnl_yen_100": -500.0,
                "miss": False,
            },
        },
        {
            "date": "20260101",
            "symbol": "2000",
            "t0": 25.0,
            "actual_filled": True,
            "fill_t": 27.0,
            "fill_price": 50.0,
            "fill_role": "ADDED",
            "path_type": "DIP_THEN_RECOVERY",
            "control_exit": {
                "reason": "SESSION_CLOSE",
                "exit_t": 100.0,
                "exit_bid": 60.0,
                "pnl_yen_100": 1000.0,
                "miss": False,
            },
            "treatment_exit": {
                "reason": "SESSION_CLOSE",
                "exit_t": 100.0,
                "exit_bid": 60.0,
                "pnl_yen_100": 1000.0,
                "miss": False,
            },
        },
    ]
    cap1_ctrl = occupancy_replay(rows, exit_key="control_exit", position_cap=1)
    cap1_treat = occupancy_replay(rows, exit_key="treatment_exit", position_cap=1)
    assert int(cap1_ctrl["fill_n"]) == 1
    assert int(cap1_treat["fill_n"]) == 2
    assert int(cap1_treat["branch_u_exit_n"]) == 1
    assert {t["symbol"] for t in cap1_treat["trades"]} == {"1000", "2000"}
    body = replay_causal_day(rows)
    assert body["control_sot_ok"] is True
    assert body["treatment_sot_ok"] is True
    assert body["pre_divergence_ok"] is True
    assert body["leftover_ok"] is True


_smoke()
assert int(POSITION_CAP) == 5

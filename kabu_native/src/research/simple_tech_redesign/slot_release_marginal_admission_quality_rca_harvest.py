"""Load frozen CASE D occupancy caches. No Capture restream. No new EXIT."""
from __future__ import annotations

from typing import Any, Optional

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_redesign.branch_u_causal_harvest import occupancy_replay
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_harvest import PATH_CACHE
from research.simple_tech_redesign.isolation import TODAY
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_spec import FROZEN_SPEC_SHA256
from small_paper.v1r_live_dual_lane import session_end_for_position


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _tid(row: dict[str, Any], *, date: Optional[str] = None) -> Optional[tuple[str, str, float]]:
    t0 = _f(row.get("t0") or row.get("signal_time"))
    if t0 is None:
        return None
    d = str(date or row.get("date") or "")
    return d, _bare(row.get("symbol")), float(t0)


def _tid_s(key: tuple[str, str, float]) -> str:
    return f"{key[0]}|{key[1]}|{key[2]}"


def load_frozen_day(day: str, *, cohort: str, today: str = TODAY) -> dict[str, Any]:
    if str(day) >= "20260903" or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    body = load_day_cache(path, FROZEN_SPEC_SHA256)
    if not body or not body.get("ok"):
        return {"ok": False, "blocker": f"frozen_cache_missing:{cohort}:{day}", "date": day}
    return body


def row_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out = {}
    for r in rows:
        key = _tid(r)
        if key is None:
            continue
        out[key] = r
    return out


def trade_index(trades: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    return row_index(trades)


def session_clock(day: str, t0: float) -> dict[str, Any]:
    open_t = float(hm_epoch(str(day), 9, 0))
    end_t = float(session_end_for_position(date=str(day), session="AM", fill_time=float(t0)))
    return {
        "seconds_from_session_open": float(t0) - open_t,
        "seconds_to_session_close": end_t - float(t0),
        "am_open": open_t,
        "am_end": end_t,
    }


def extract_cap_inventory(day: str, body: dict[str, Any]) -> dict[str, Any]:
    rows_by = row_index(list(body.get("rows") or []))
    ctrl = dict(body.get("control") or {})
    cap_only: list[dict[str, Any]] = []
    multi: list[dict[str, Any]] = []
    same_sym: list[dict[str, Any]] = []
    cap_n = 0
    same_n = 0
    for ev in list(ctrl.get("event_log") or []):
        kind = str(ev[1]) if len(ev) > 1 else ""
        if kind not in {"CAP", "SAME_SYMBOL"} or len(ev) < 4:
            continue
        t0 = _f(ev[3])
        if t0 is None:
            continue
        key = (str(day), _bare(ev[2]), float(t0))
        row = dict(rows_by.get(key) or {})
        clock = session_clock(day, float(t0))
        rec = {
            "date": day,
            "symbol": key[1],
            "t0": key[2],
            "trade_id": _tid_s(key),
            "block_t": ev[0],
            "block_reason": kind,
            "executable_signal": bool(row.get("executable_signal")) if row else False,
            "actual_filled": bool(row.get("actual_filled")) if row else False,
            "fill_role": row.get("fill_role"),
            "path_type": row.get("path_type"),
            "fill_t": row.get("fill_t"),
            "fill_price": row.get("fill_price"),
            "session_close_pnl": (dict(row.get("control_exit") or {}).get("pnl_yen_100") if row else None),
            "row_found": bool(row),
            **clock,
        }
        if kind == "SAME_SYMBOL":
            same_n += 1
            same_sym.append(rec)
            continue
        cap_n += 1
        exec_ok = bool(rec["executable_signal"]) and bool(rec["row_found"])
        if not exec_ok:
            rec["exclude_reason"] = "CAP_PLUS_NOT_EXECUTION_ELIGIBLE" if rec["row_found"] else "CAP_ROW_MISSING"
            multi.append(rec)
            continue
        rec["exclude_reason"] = None
        cap_only.append(rec)
    return {
        "date": day,
        "cap_event_n": cap_n,
        "same_symbol_event_n": same_n,
        "cap_only": cap_only,
        "multi_reason": multi,
        "same_symbol": same_sym,
    }


def assign_generations(day: str, ctrl_trades: list[dict[str, Any]], treat: dict[str, Any]) -> list[dict[str, Any]]:
    ctrl_ids = set()
    for t in ctrl_trades:
        key = _tid(t)
        if key is not None:
            ctrl_ids.add(key)
    tm = trade_index(list(treat.get("trades") or []))
    gen: dict[tuple[str, str, float], int] = {}
    parent: dict[tuple[str, str, float], Optional[tuple[str, str, float]]] = {}
    root: dict[tuple[str, str, float], Optional[tuple[str, str, float]]] = {}
    parent_reason: dict[tuple[str, str, float], Optional[str]] = {}
    parent_exit_t: dict[tuple[str, str, float], Any] = {}
    for key in ctrl_ids:
        gen[key] = 0
        parent[key] = None
        root[key] = key
        parent_reason[key] = None
        parent_exit_t[key] = None
    last_exit_key: Optional[tuple[str, str, float]] = None
    last_reason: Optional[str] = None
    last_exit_t: Any = None
    for ev in list(treat.get("event_log") or []):
        kind = str(ev[1]) if len(ev) > 1 else ""
        if kind == "EXIT" and len(ev) >= 4:
            t0 = _f(ev[3])
            if t0 is None:
                continue
            last_exit_key = (str(day), _bare(ev[2]), float(t0))
            last_reason = str(ev[4]) if len(ev) > 4 else None
            last_exit_t = ev[0]
            continue
        if kind != "ADMIT" or len(ev) < 4:
            continue
        t0 = _f(ev[3])
        if t0 is None:
            continue
        key = (str(day), _bare(ev[2]), float(t0))
        if key in ctrl_ids or key not in tm:
            continue
        parent[key] = last_exit_key
        parent_reason[key] = last_reason
        parent_exit_t[key] = last_exit_t
        pgen = gen.get(last_exit_key) if last_exit_key is not None else None
        if pgen is None:
            gen[key] = 1
            root[key] = last_exit_key
        else:
            gen[key] = int(pgen) + 1
            root[key] = root.get(last_exit_key) or last_exit_key
    out = []
    for key, t in tm.items():
        g = int(gen.get(key, 0 if key in ctrl_ids else 1))
        rt = root.get(key)
        pt = parent.get(key)
        rec = {
            "date": key[0],
            "symbol": key[1],
            "t0": key[2],
            "trade_id": _tid_s(key),
            "generation": g,
            "chain_id": _tid_s(rt) if rt is not None else None,
            "root_control_exit_trade_id": _tid_s(rt) if rt is not None else None,
            "parent_fill_trade_id": _tid_s(pt) if pt is not None else None,
            "parent_exit_reason": parent_reason.get(key),
            "parent_exit_t": parent_exit_t.get(key),
            "is_control_shared": key in ctrl_ids,
            "is_incremental": key not in ctrl_ids,
            "fill_role": t.get("fill_role"),
            "path_type": t.get("path_type"),
            "exit_reason": t.get("exit_reason"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "fill_time": t.get("fill_time"),
            "exit_time": t.get("exit_time"),
            "admit_t": key[2] if key not in ctrl_ids else t.get("fill_time"),
        }
        out.append(rec)
    return out


def one_generation_rows(rows: list[dict[str, Any]], ctrl_ids: set[tuple[str, str, float]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        key = _tid(rec)
        if key is not None and key not in ctrl_ids:
            rec["treatment_exit"] = dict(rec.get("control_exit") or {}) if rec.get("control_exit") else None
        out.append(rec)
    return out


def harvest_day(day: str, *, cohort: str, today: str = TODAY) -> dict[str, Any]:
    body = load_frozen_day(day, cohort=cohort, today=today)
    if not body.get("ok"):
        return body
    ctrl_tr = list((body.get("control") or {}).get("trades") or [])
    ctrl_ids = {k for k in (_tid(t) for t in ctrl_tr) if k is not None}
    cap = extract_cap_inventory(day, body)
    chains = assign_generations(day, ctrl_tr, dict(body.get("treatment") or {}))
    og_rows = one_generation_rows(list(body.get("rows") or []), ctrl_ids)
    og = occupancy_replay(og_rows, exit_key="treatment_exit")
    ctrl_sot = occupancy_replay(list(body.get("rows") or []), exit_key="control_exit")
    return {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "rows": list(body.get("rows") or []),
        "control": body.get("control"),
        "treatment": body.get("treatment"),
        "incremental_causes": list(body.get("incremental_causes") or []),
        "control_sot_ok": body.get("control_sot_ok"),
        "treatment_sot_ok": body.get("treatment_sot_ok"),
        "leftover_ok": bool(body.get("leftover_ok"))
        and int(ctrl_sot.get("open_leftover_n") or 0) == 0
        and int(ctrl_sot.get("pending_leftover_n") or 0) == 0,
        "cap_inventory": cap,
        "chains": chains,
        "one_generation": {
            "fill_n": og.get("fill_n"),
            "trades": og.get("trades"),
            "cap_blocked": og.get("cap_blocked"),
            "same_symbol_blocked": og.get("same_symbol_blocked"),
            "slot_release_n": og.get("slot_release_n"),
            "open_leftover_n": og.get("open_leftover_n"),
            "pending_leftover_n": og.get("pending_leftover_n"),
        },
        "control_replay_fill_n": ctrl_sot.get("fill_n"),
        "control_replay_match": int(ctrl_sot.get("fill_n") or 0) == int((body.get("control") or {}).get("fill_n") or -1),
    }


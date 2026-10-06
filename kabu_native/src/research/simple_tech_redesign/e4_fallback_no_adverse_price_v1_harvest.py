"""Harvest E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1 from exact V26 E4 + Ask fallback."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family import v13_harvest as v13_mod
from research.simple_tech_redesign.branch_u_causal_harvest import occupancy_replay, sot_parity
from research.simple_tech_redesign.causal_board_rca_harvest import load_u_day
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_spec import (
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    TICK_TOLERANCE,
)
from research.simple_tech_redesign.v24_harvest import _finite, load_v22_day_rows
from research.simple_tech_redesign.v26_harvest import V26_CACHE, load_v26_day_cache, replay_joint_day
from research.simple_tech_redesign.v26_spec import E4_WAIT_BUDGET_SEC, spec_sha256_v26
from research.simple_tech_redesign import v26_harvest as v26_mod

PATH_CACHE = RESEARCH_CACHE / "e4_fallback_no_adverse_price_v1"
PX_EPS = 1e-9
V13_PATH = Path(v13_mod.__file__).resolve()
V26_PATH = Path(v26_mod.__file__).resolve()


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def recover_e4_fallback_source() -> dict[str, Any]:
    if not V13_PATH.is_file() or not V26_PATH.is_file():
        return {"ok": False, "blocker": "SOURCE_FILE_MISSING"}
    v13 = V13_PATH.read_text(encoding="utf-8")
    v26 = V26_PATH.read_text(encoding="utf-8")
    if "def simulate_e4_only" not in v13:
        return {"ok": False, "blocker": "SIMULATE_E4_ONLY_NOT_FOUND"}
    if "limit_price=float(inside_lim)" not in v13 or 'rec["inside_limit"] = float(inside_lim)' not in v13:
        return {"ok": False, "blocker": "E4_LIMIT_ASSIGNMENT_NOT_FOUND"}
    if "improve_1tick_limit(bid0, ask0)" not in v13:
        return {"ok": False, "blocker": "IMPROVE_1TICK_NOT_FOUND"}
    if "def simulate_ask_fallback" not in v26:
        return {"ok": False, "blocker": "SIMULATE_ASK_FALLBACK_NOT_FOUND"}
    if "t_fb = float(t0) + float(E4_WAIT_BUDGET_SEC)" not in v26:
        return {"ok": False, "blocker": "W5_TIMESTAMP_NOT_FOUND"}
    if 'pack["fill_price"] = float(ask)' not in v26 or 'pack["limit_price"] = float(ask)' not in v26:
        return {"ok": False, "blocker": "FALLBACK_ASK_FILL_NOT_FOUND"}
    if 'np.searchsorted(t, t_fb, side="right") - 1' not in v26:
        return {"ok": False, "blocker": "W5_BOARD_INDEX_NOT_FOUND"}
    if "age > float(CANONICAL_FRESHNESS_SEC)" not in v26:
        return {"ok": False, "blocker": "ASK_FRESHNESS_NOT_FOUND"}
    tree13 = ast.parse(v13)
    tree26 = ast.parse(v26)
    e4_line = None
    fb_line = None
    for node in ast.walk(tree13):
        if isinstance(node, ast.FunctionDef) and node.name == "simulate_e4_only":
            e4_line = int(node.lineno)
    for node in ast.walk(tree26):
        if isinstance(node, ast.FunctionDef) and node.name == "simulate_ask_fallback":
            fb_line = int(node.lineno)
    if e4_line is None or fb_line is None:
        return {"ok": False, "blocker": "LINE_RESTORE_FAILED"}
    return {
        "ok": True,
        "SOURCE_FILE": "src/research/simple_tech_entry_family/v13_harvest.py + src/research/simple_tech_redesign/v26_harvest.py",
        "SOURCE_FUNCTION": "simulate_e4_only / simulate_ask_fallback",
        "SOURCE_SHA": _sha(V13_PATH) + ";" + _sha(V26_PATH),
        "E4_SOURCE_FILE": "src/research/simple_tech_entry_family/v13_harvest.py",
        "E4_SOURCE_LINE": f"simulate_e4_only@{e4_line}",
        "FALLBACK_SOURCE_FILE": "src/research/simple_tech_redesign/v26_harvest.py",
        "FALLBACK_SOURCE_LINE": f"simulate_ask_fallback@{fb_line}",
        "EXACT_E4_LIMIT_PRICE": (
            "simulate_e4_only: inside_lim, collapsed, tick = improve_1tick_limit(bid0, ask0); "
            "rec['inside_limit']=inside_lim; E4 arm limit_price=inside_lim; "
            "standalone_fill(..., limit_price=inside_lim) via find_ask_cross_fill; "
            "fill_price=limit_price (no improvement). "
            "improve_1tick_limit uses inside_limit_price(bid+1 JPX tick if strictly inside spread) else collapses to bid0."
        ),
        "EXACT_WAIT_START": "t0 = signal time from quote_row / corrected_row. Wait budget starts at t0.",
        "EXACT_5S_ELIGIBILITY": (
            f"E4_WAIT_BUDGET_SEC={float(E4_WAIT_BUDGET_SEC)}. "
            "E4 scans [t0, t0+5] for Ask1 <= E4 limit. Fallback only if not e4_filled. "
            "t_fb = t0 + E4_WAIT_BUDGET_SEC; skipped if t_fb > AM session end."
        ),
        "EXACT_ASK1_FRESHNESS": (
            "Last board snapshot with t <= t_fb (searchsorted right-1). "
            "Reject FUTURE_BOARD/FUTURE_CLOCK. age = t_fb - board_clock; "
            "require executable, not special, age <= CANONICAL_FRESHNESS_SEC (5s), ask>0, ask_qty>=100."
        ),
        "EXACT_FALLBACK_EVENT_T": "pack['fill_t'] = t_fb (exactly t0+5), not the board snapshot time.",
        "EXACT_FILL_SEMANTICS": (
            "Control fallback: one-shot marketable limit at W5 Ask1; fill_price=ask; no chase/reprice. "
            "Treatment: same one-shot iff ask <= EXACT_E4_LIMIT_PRICE; else NO_FILL. 0 tick tolerance."
        ),
        "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
        "TICK_TOLERANCE": int(TICK_TOLERANCE),
    }


def _tid(row: dict[str, Any]) -> Optional[tuple[str, str, float]]:
    t0 = _f(row.get("t0") or row.get("signal_time"))
    if t0 is None:
        return None
    return str(row.get("date") or ""), _bare(row.get("symbol")), float(t0)


def _e4_limit(row: dict[str, Any]) -> Optional[float]:
    e4 = dict(row.get("e4") or {})
    return _f(e4.get("limit_price")) or _f(row.get("inside_limit")) or _f(row.get("e4_limit_price"))


def _path_type(row: dict[str, Any]) -> str:
    p = str(row.get("path_type") or "OTHER")
    return p if p else "OTHER"


def _bps(ask: Optional[float], lim: Optional[float]) -> Optional[float]:
    if ask is None or lim is None or float(lim) <= 0.0:
        return None
    return (float(ask) / float(lim) - 1.0) * 10000.0


def _degrade(row: dict[str, Any]) -> dict[str, Any]:
    lim = _e4_limit(row)
    fb = dict(row.get("ask_fallback") or {})
    ask = _f(fb.get("ask"))
    tick = float(jpx_tick_size_yen(float(lim))) if lim is not None else None
    delta = (float(ask) - float(lim)) if ask is not None and lim is not None else None
    return {
        "e4_limit_price": lim,
        "w5_ask1": ask,
        "delta_yen_per_share": delta,
        "delta_yen_100": (delta * 100.0) if delta is not None else None,
        "delta_ticks": (delta / tick) if delta is not None and tick else None,
        "delta_bps": _bps(ask, lim),
        "tick_size": tick,
        "collapsed_to_bid": bool((row.get("e4") or {}).get("collapsed_to_bid")),
    }


def apply_treatment(row: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(row)
    lim = _e4_limit(row)
    out["e4_limit_price"] = lim
    fb = dict(row.get("ask_fallback") or {})
    out["w5_ask1"] = _f(fb.get("ask"))
    out["price_degrade"] = _degrade(row)
    if bool(row.get("e4_filled")) or str(row.get("fill_role") or "") == "CORE":
        out["exec_class"] = "CORE_E4_FILL"
        out["w5_decision"] = "NOT_APPLICABLE_CORE"
        return out
    if bool(fb.get("eligible")) and bool(fb.get("filled")):
        if lim is None:
            leak["E4_LIMIT_MISSING_N"] = int(leak.get("E4_LIMIT_MISSING_N") or 0) + 1
            out["exec_class"] = "E4_LIMIT_MISSING"
            out["w5_decision"] = "INTEGRITY_FAIL"
            return out
        ask = _f(fb.get("ask"))
        if ask is None:
            leak["W5_ASK_MISSING_N"] = int(leak.get("W5_ASK_MISSING_N") or 0) + 1
            out["exec_class"] = "W5_ASK_MISSING"
            out["w5_decision"] = "INTEGRITY_FAIL"
            return out
        if float(ask) <= float(lim) + PX_EPS:
            out["exec_class"] = "W5_ACCEPTED_NO_ADVERSE"
            out["w5_decision"] = "ACCEPT"
            return out
        out["exec_class"] = "W5_REJECTED_ADVERSE_PRICE"
        out["w5_decision"] = "REJECT"
        out["actual_filled"] = False
        out["fill_role"] = None
        out["fill_t"] = None
        out["fill_price"] = None
        out["control_exit"] = None
        out["treatment_exit"] = None
        return out
    out["exec_class"] = "NO_FALLBACK_FILL"
    out["w5_decision"] = "NOT_ELIGIBLE"
    return out


def tag_control(row: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(row)
    out["e4_limit_price"] = _e4_limit(row)
    fb = dict(row.get("ask_fallback") or {})
    out["w5_ask1"] = _f(fb.get("ask"))
    out["price_degrade"] = _degrade(row)
    if bool(row.get("e4_filled")) or str(row.get("fill_role") or "") == "CORE":
        out["exec_class"] = "CORE_E4_FILL"
        out["w5_decision"] = "NOT_APPLICABLE_CORE"
    elif bool(fb.get("eligible")) and bool(fb.get("filled")):
        out["exec_class"] = "W5_FALLBACK_CONTROL_FILL"
        out["w5_decision"] = "CONTROL_ACCEPT"
    else:
        out["exec_class"] = "NO_FALLBACK_FILL"
        out["w5_decision"] = "NOT_ELIGIBLE"
    out["treatment_exit"] = dict(out.get("control_exit") or {}) if out.get("actual_filled") else None
    return out


def _index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out = {}
    for r in rows:
        k = _tid(r)
        if k is not None:
            out[k] = r
    return out


def _merge_exec(u_row: dict[str, Any], exec_row: dict[str, Any], leak: dict[str, Any]) -> dict[str, Any]:
    """Keep Control fill/exit identity from Branch U. Overlay exact E4 limit + W5 Ask only."""
    rec = copy.deepcopy(u_row)
    u_e4 = dict(u_row.get("e4") or {})
    e_e4 = dict(exec_row.get("e4") or {})
    u_lim = _f(u_e4.get("limit_price")) or _f(u_row.get("inside_limit"))
    e_lim = _f(e_e4.get("limit_price")) or _f(exec_row.get("inside_limit"))
    if u_lim is not None and e_lim is not None and abs(float(u_lim) - float(e_lim)) > PX_EPS:
        leak["E4_LIMIT_DRIFT_N"] = int(leak.get("E4_LIMIT_DRIFT_N") or 0) + 1
    merged_e4 = dict(u_e4)
    if e_e4:
        merged_e4.update({k: v for k, v in e_e4.items() if v is not None})
    if e_lim is not None and merged_e4.get("limit_price") is None:
        merged_e4["limit_price"] = e_lim
    rec["e4"] = merged_e4
    rec["ask_fallback"] = dict(exec_row.get("ask_fallback") or {})
    rec["inside_limit"] = u_lim if u_lim is not None else e_lim
    rec["fill_path"] = exec_row.get("fill_path") or rec.get("fill_path")
    rec["path_type"] = exec_row.get("path_type") or rec.get("path_type")
    rec["e4_filled"] = bool(u_row.get("e4_filled")) or str(u_row.get("fill_role") or "") == "CORE"
    u_fill = bool(u_row.get("actual_filled"))
    e_fill = bool(exec_row.get("actual_filled"))
    if u_fill != e_fill or str(u_row.get("fill_role") or "") != str(exec_row.get("fill_role") or ""):
        leak["EXECUTION_U_V26_DRIFT_N"] = int(leak.get("EXECUTION_U_V26_DRIFT_N") or 0) + 1
    if u_fill and not dict(rec.get("control_exit") or {}).get("exit_t"):
        leak["SESSION_CLOSE_MISSING_N"] = int(leak.get("SESSION_CLOSE_MISSING_N") or 0) + 1
    if str(u_row.get("fill_role") or "") == "ADDED":
        fb = dict(rec.get("ask_fallback") or {})
        if _f(fb.get("ask")) is None:
            leak["W5_ASK_MISSING_N"] = int(leak.get("W5_ASK_MISSING_N") or 0) + 1
        if _e4_limit(rec) is None:
            leak["E4_LIMIT_MISSING_N"] = int(leak.get("E4_LIMIT_MISSING_N") or 0) + 1
    return rec


def _signals_for_joint(u_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in u_rows:
        out.append(
            {
                "date": r.get("date"),
                "session": "AM",
                "symbol": _bare(r.get("symbol")),
                "t0": r.get("t0"),
                "executable_signal": r.get("executable_signal"),
                "board_ok": r.get("board_ok"),
                "ask_reason": r.get("ask_reason"),
                "cohort": r.get("v22_cohort") or r.get("cohort"),
                "trend": r.get("trend"),
                "pullback": r.get("pullback"),
                "rci": r.get("rci"),
            }
        )
    return out


def load_execution_rows(day: str, *, cohort: str, leak: dict[str, Any]) -> dict[str, Any]:
    ubody = load_u_day(day, cohort=cohort)
    u_rows = [dict(r) for r in list(ubody.get("rows") or [])]
    if not u_rows:
        return {"ok": False, "blocker": f"u_cache_missing:{day}"}
    v26_sha = spec_sha256_v26()
    exec_rows: list[dict[str, Any]]
    if cohort == "DEVELOPMENT":
        cached = load_v26_day_cache(V26_CACHE / f"day_{day}.json", v26_sha)
        if not cached or not list(cached.get("rows") or []):
            v22 = load_v22_day_rows(day)
            cap = find_capture_dir(day)
            if cap is None:
                return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}"}
            body = replay_joint_day({"date": day, "capture_path": str(cap), "signals": v22, "spec_sha": v26_sha})
            if not body.get("ok"):
                return {"ok": False, "blocker": f"v26_replay:{body.get('blocker')}"}
            exec_rows = list(body.get("rows") or [])
        else:
            exec_rows = list(cached.get("rows") or [])
    else:
        cap = find_capture_dir(day)
        if cap is None:
            return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}"}
        sigs = _signals_for_joint(u_rows)
        print(f"{cohort} {day} e4-fallback restream signals={len(sigs)}", flush=True)
        body = replay_joint_day({"date": day, "capture_path": str(cap), "signals": sigs, "spec_sha": v26_sha})
        if not body.get("ok"):
            return {"ok": False, "blocker": f"v26_replay:{body.get('blocker')}"}
        exec_rows = list(body.get("rows") or [])
    u_ix = _index(u_rows)
    e_ix = _index(exec_rows)
    if set(u_ix) != set(e_ix):
        leak["SIGNAL_SET_DRIFT_N"] = int(leak.get("SIGNAL_SET_DRIFT_N") or 0) + 1
        return {"ok": False, "blocker": f"signal_set_mismatch:{day}"}
    merged = [_merge_exec(u_ix[k], e_ix[k], leak) for k in sorted(u_ix)]
    return {"ok": True, "rows": merged}


def _occ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    occ = occupancy_replay(rows, exit_key="control_exit")
    ok = sot_parity(rows, exit_key="control_exit", ours=occ)
    leftover = int(occ.get("open_leftover_n") or 0) == 0 and int(occ.get("pending_leftover_n") or 0) == 0
    return {"occupancy": occ, "sot_ok": bool(ok), "leftover_ok": bool(leftover)}


def harvest_day(day: str, *, cohort: str, spec_sha: str, pred_pack: dict[str, Any], today: str = TODAY) -> dict[str, Any]:
    if str(day) in FORBIDDEN_INPUT_DAYS or str(day) > str(MAX_RESEARCH_DATE) or day == str(today):
        return {"ok": False, "blocker": "FORBIDDEN_OR_TODAY", "date": day}
    if not pred_pack.get("ok"):
        return {"ok": False, "blocker": pred_pack.get("blocker") or "SOURCE_RESTORE_FAILED", "date": day}
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    leak = {
        "E4_LIMIT_MISSING_N": 0,
        "E4_LIMIT_DRIFT_N": 0,
        "W5_ASK_MISSING_N": 0,
        "EXECUTION_U_V26_DRIFT_N": 0,
        "SESSION_CLOSE_MISSING_N": 0,
        "SIGNAL_SET_DRIFT_N": 0,
        "FUTURE_QUOTE_CARRYBACK_N": 0,
        "FUTURE_TIMESTAMP_CARRYBACK_N": 0,
        "THRESHOLD_SEARCH_N": 0,
    }
    loaded = load_execution_rows(day, cohort=cohort, leak=leak)
    if not loaded.get("ok"):
        return {"ok": False, "blocker": loaded.get("blocker"), "date": day, "leak": leak}
    raw = list(loaded.get("rows") or [])
    ctrl_rows = [tag_control(r) for r in raw]
    treat_rows = [apply_treatment(r, leak) for r in raw]
    if (
        int(leak.get("E4_LIMIT_MISSING_N") or 0)
        or int(leak.get("W5_ASK_MISSING_N") or 0)
        or int(leak.get("E4_LIMIT_DRIFT_N") or 0)
        or int(leak.get("SESSION_CLOSE_MISSING_N") or 0)
    ):
        return {"ok": False, "blocker": "E4_LIMIT_OR_W5_ASK_UNRECOVERABLE", "date": day, "leak": leak}
    core_rows = []
    for r in ctrl_rows:
        c = copy.deepcopy(r)
        if str(c.get("fill_role") or "") != "CORE":
            c["actual_filled"] = False
            c["fill_role"] = None
            c["control_exit"] = None
        core_rows.append(c)
    print(
        f"{cohort} {day} e4-fallback signals={len(ctrl_rows)} "
        f"ctrl_unconst_fill={sum(1 for r in ctrl_rows if r.get('actual_filled'))} "
        f"treat_unconst_fill={sum(1 for r in treat_rows if r.get('actual_filled'))}",
        flush=True,
    )
    ctrl = _occ(ctrl_rows)
    treat = _occ(treat_rows)
    core_only = _occ(core_rows)
    if not ctrl.get("sot_ok"):
        return {"ok": False, "blocker": f"control_occupancy_sot_fail:{day}", "date": day, "leak": leak}
    unconstrained = []
    for c, t in zip(ctrl_rows, treat_rows):
        unconstrained.append(
            {
                "date": c.get("date"),
                "symbol": _bare(c.get("symbol")),
                "t0": c.get("t0"),
                "ctrl_exec_class": c.get("exec_class"),
                "treat_exec_class": t.get("exec_class"),
                "w5_decision": t.get("w5_decision"),
                "fill_role_ctrl": c.get("fill_role"),
                "path_type": _path_type(c),
                "ctrl_filled": bool(c.get("actual_filled")),
                "treat_filled": bool(t.get("actual_filled")),
                "e4_limit_price": c.get("e4_limit_price"),
                "w5_ask1": c.get("w5_ask1"),
                "price_degrade": c.get("price_degrade"),
                "ctrl_pnl": (c.get("control_exit") or {}).get("pnl_yen_100"),
                "mfe": ((c.get("fill_path") or {}) if isinstance(c.get("fill_path"), dict) else {}).get("mfe"),
                "mae": ((c.get("fill_path") or {}) if isinstance(c.get("fill_path"), dict) else {}).get("mae"),
            }
        )
    slim_ctrl = []
    for r in ctrl_rows:
        slim_ctrl.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "t0": r.get("t0"),
                "fill_role": r.get("fill_role"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "actual_filled": r.get("actual_filled"),
                "executable_signal": r.get("executable_signal"),
                "path_type": r.get("path_type"),
                "exec_class": r.get("exec_class"),
                "e4_limit_price": r.get("e4_limit_price"),
                "w5_ask1": r.get("w5_ask1"),
                "control_exit": r.get("control_exit"),
                "treatment_exit": r.get("treatment_exit"),
            }
        )
    body = {
        "ok": True,
        "date": day,
        "cohort": cohort,
        "spec_sha": spec_sha,
        "rows": slim_ctrl,
        "unconstrained": unconstrained,
        "leak": leak,
        "control": _arm_public(dict(ctrl.get("occupancy") or {})),
        "treatment": _arm_public(dict(treat.get("occupancy") or {})),
        "core_only": _arm_public(dict(core_only.get("occupancy") or {})),
        "control_sot_ok": ctrl.get("sot_ok"),
        "treatment_sot_ok": treat.get("sot_ok"),
        "core_only_sot_ok": core_only.get("sot_ok"),
        "leftover_ok": bool(ctrl.get("leftover_ok") and treat.get("leftover_ok") and core_only.get("leftover_ok")),
    }
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return body


def _arm_public(arm: dict[str, Any]) -> dict[str, Any]:
    body = dict(arm or {})
    body.pop("candidates", None)
    body.pop("event_log", None)
    return body

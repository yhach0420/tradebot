"""Full causal portfolio economics for POST_BE_CONFIRMED_SWING_FLOOR_V1."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.simple_tech_redesign.branch_u_causal_analyze import _tid_map, attribution
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import (
    EPS,
    _f,
    _pf_gt,
    arm_econ,
    day_table,
    delta_pack,
    identity_check,
    residual_index,
    winner_class_pack,
)
from research.simple_tech_redesign.exit_residual_rca_harvest import residual_class
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_spec import (
    CANDIDATE_EXIT_REASON,
    CANDIDATE_ID,
    CONCENTRATION_MAX_SHARE,
    DECOMP_TOL,
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    PATH_TYPES,
    RESIDUAL_CLASSES,
)
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import _tid, _tid_s
from research.simple_tech_strategy.v20_analyze import daily_yen

CLASS_SHORT = {
    "U_EARLY_NEVER_BE": "U",
    "P_EARLY_AFTER_BE": "P_EARLY",
    "P_PROFIT_THEN_FAILURE": "PTF",
    "PROTECTED_DIP": "DIP",
    "PROTECTED_GOOD": "GOOD",
}


def _path(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("candidate_path") or {})


def class_of(
    key: tuple[str, str, float], residual: dict[tuple[str, str, float], dict[str, Any]], harvest: dict[str, Any]
) -> str:
    cls = str((residual.get(key) or {}).get("residual_class") or "")
    if cls in CLASS_SHORT:
        return cls
    p = str(harvest.get("path_type") or (residual.get(key) or {}).get("path_type") or "OTHER")
    be = bool(_path(harvest).get("be_reached"))
    if (residual.get(key) or {}).get("break_even_reached") is not None:
        be = bool((residual.get(key) or {}).get("break_even_reached"))
    return residual_class(path_type=p, be_reached=be)


def path_of(
    key: tuple[str, str, float], residual: dict[tuple[str, str, float], dict[str, Any]], harvest: dict[str, Any]
) -> str:
    p = str(harvest.get("path_type") or (residual.get(key) or {}).get("path_type") or "OTHER")
    return p if p in PATH_TYPES else "OTHER"


def arm_counts(arm: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    tech = sum(1 for t in trades if str(t.get("exit_reason") or "") == CANDIDATE_EXIT_REASON)
    close_n = sum(1 for t in trades if str(t.get("exit_reason") or "") != CANDIDATE_EXIT_REASON)
    return {
        "fill_n": int(arm.get("fill_n") or len(trades)),
        "exit_n": int(arm.get("slot_release_n") or len(trades)),
        "session_close_n": int(close_n),
        "technical_exit_n": int(tech),
        "slot_release_n": int(arm.get("slot_release_n") or 0),
        "CAP_reject_n": int(arm.get("cap_blocked") or 0),
        "same_symbol_reject_n": int(arm.get("same_symbol_blocked") or 0),
        "core_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE"),
        "added_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED"),
        "open_leftover_n": int(arm.get("open_leftover_n") or 0),
        "pending_leftover_n": int(arm.get("pending_leftover_n") or 0),
    }


def portfolio_pack(arm: dict[str, Any], days: list[str]) -> dict[str, Any]:
    trades = list(arm.get("trades") or [])
    econ = arm_econ(trades)
    pnls = [float(_f(t.get("pnl_yen_100")) or 0.0) for t in trades]
    n = len(pnls)
    econ["avg_pnl"] = (sum(pnls) / n) if n else None
    econ["median_pnl"] = float(median(pnls)) if pnls else None
    daily = daily_yen(trades, list(days))
    econ["positive_day_n"] = sum(1 for r in daily if float(r.get("net_pnl") or 0.0) > EPS)
    econ["negative_day_n"] = sum(1 for r in daily if float(r.get("net_pnl") or 0.0) < -EPS)
    counts = arm_counts(arm, trades)
    return {**counts, **econ}


def group_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(r.get("delta") or 0.0) for r in rows]
    trig = [r for r in rows if bool(r.get("triggered"))]
    pack = delta_pack(deltas)
    pack["N"] = len(rows)
    pack["EXIT_TRIGGER_N"] = len(trig)
    pack["control_pnl"] = float(sum(float(r.get("ctrl_pnl") or 0.0) for r in rows))
    pack["treatment_pnl"] = float(sum(float(r.get("treat_pnl") or 0.0) for r in rows))
    pack["DIRECT_DELTA"] = float(sum(deltas))
    trig_d = [float(r.get("delta") or 0.0) for r in trig]
    pack["trigger_direct_delta"] = float(sum(trig_d)) if trig_d else 0.0
    pack["trigger_median_delta"] = float(median(trig_d)) if trig_d else None
    return pack


def _abs_top(items: list[tuple[Any, float]]) -> dict[str, Any]:
    abs_sum = float(sum(abs(v) for _, v in items))
    top = max(items, key=lambda x: abs(x[1]), default=None)
    share = (abs(float(top[1])) / abs_sum) if top is not None and abs_sum > EPS else None
    return {
        "top": None if top is None else top[0],
        "value": None if top is None else float(top[1]),
        "abs_share": share,
        "warning_gt_50pct": bool(share is not None and float(share) > CONCENTRATION_MAX_SHARE + 1e-15),
    }


def concentration_pack(
    days: list[dict[str, Any]],
    ctrl_trades: list[dict[str, Any]],
    treat_trades: list[dict[str, Any]],
    *,
    common_rows: list[dict[str, Any]],
    treat_only: list[dict[str, Any]],
    ctrl_only: list[dict[str, Any]],
    total: float,
    downstream: float,
    displaced: float,
    direct: float,
) -> dict[str, Any]:
    day_direct: dict[str, float] = defaultdict(float)
    day_down: dict[str, float] = defaultdict(float)
    day_disp: dict[str, float] = defaultdict(float)
    for r in common_rows:
        day_direct[str(r.get("date") or "")] += float(r.get("delta") or 0.0)
    for t in treat_only:
        day_down[str(t.get("date") or "")] += float(t.get("pnl_yen_100") or 0.0)
    for t in ctrl_only:
        day_disp[str(t.get("date") or "")] += 0.0 - float(t.get("pnl_yen_100") or 0.0)
    day_total = {str(d.get("date") or ""): float(d.get("delta") or 0.0) for d in days}
    sym_direct: dict[str, float] = defaultdict(float)
    sym_down: dict[str, float] = defaultdict(float)
    sym_disp: dict[str, float] = defaultdict(float)
    for r in common_rows:
        sym_direct[str(r.get("symbol") or "")] += float(r.get("delta") or 0.0)
    for t in treat_only:
        sym_down[str(t.get("symbol") or "")] += float(t.get("pnl_yen_100") or 0.0)
    for t in ctrl_only:
        sym_disp[str(t.get("symbol") or "")] += 0.0 - float(t.get("pnl_yen_100") or 0.0)
    cm = _tid_map(ctrl_trades)
    tm = _tid_map(treat_trades)
    sym_total: dict[str, float] = defaultdict(float)
    for k, t in tm.items():
        c = cm.get(k)
        c_pnl = float(c.get("pnl_yen_100") or 0.0) if c else 0.0
        sym_total[k[1]] += float(t.get("pnl_yen_100") or 0.0) - c_pnl
    for k, c in cm.items():
        if k not in tm:
            sym_total[k[1]] += 0.0 - float(c.get("pnl_yen_100") or 0.0)
    out = {
        "day": {
            "direct": _abs_top(list(day_direct.items())),
            "downstream": _abs_top(list(day_down.items())),
            "displaced": _abs_top(list(day_disp.items())),
            "total": _abs_top(list(day_total.items())),
        },
        "symbol": {
            "direct": _abs_top(list(sym_direct.items())),
            "downstream": _abs_top(list(sym_down.items())),
            "displaced": _abs_top(list(sym_disp.items())),
            "total": _abs_top(list(sym_total.items())),
        },
        "full_total_causal_delta": float(total),
        "DIRECT_EXIT_DELTA": float(direct),
        "SLOT_RELEASE_DOWNSTREAM_DELTA": float(downstream),
        "DISPLACED_TRADE_DELTA": float(displaced),
        "exclusion_applied": False,
        "symbol_285A_excluded": False,
    }
    warns = []
    for axis in ("day", "symbol"):
        for part in ("direct", "downstream", "total"):
            if bool(((out[axis] or {}).get(part) or {}).get("warning_gt_50pct")):
                warns.append(f"{axis}.{part}")
    out["concentration_warning_gt_50pct"] = bool(warns)
    out["warning_parts"] = warns
    share_total_day = (out["day"]["total"] or {}).get("abs_share")
    share_total_sym = (out["symbol"]["total"] or {}).get("abs_share")
    out["single_day_contribution_gt_50pct"] = bool(
        (share_total_day is not None and float(share_total_day) > CONCENTRATION_MAX_SHARE + 1e-15)
        or ((out["day"]["direct"] or {}).get("warning_gt_50pct"))
        or ((out["day"]["downstream"] or {}).get("warning_gt_50pct"))
    )
    out["single_symbol_contribution_gt_50pct"] = bool(
        (share_total_sym is not None and float(share_total_sym) > CONCENTRATION_MAX_SHARE + 1e-15)
        or ((out["symbol"]["direct"] or {}).get("warning_gt_50pct"))
        or ((out["symbol"]["downstream"] or {}).get("warning_gt_50pct"))
    )
    return out


def evaluate_cohort(
    *,
    cohort: str,
    days: list[str],
    harvest_rows: list[dict[str, Any]],
    ctrl_arm: dict[str, Any],
    treat_arm: dict[str, Any],
    residual: dict[tuple[str, str, float], dict[str, Any]],
    causes: list[dict[str, Any]],
    leftover_ok: bool,
    control_sot_ok: bool,
    treatment_sot_ok: bool,
) -> dict[str, Any]:
    ctrl_tr = list(ctrl_arm.get("trades") or [])
    treat_tr = list(treat_arm.get("trades") or [])
    rows_by = residual_index(harvest_rows)
    if cohort == "DEVELOPMENT":
        ident = identity_check(ctrl_tr, fill_n=DEV_FILL_N, core_n=DEV_CORE_N, added_n=DEV_ADDED_N, pnl=DEV_PNL)
    else:
        ident = identity_check(ctrl_tr, fill_n=FWD_FILL_N, core_n=FWD_CORE_N, added_n=FWD_ADDED_N, pnl=FWD_PNL)
    ident["control_sot_ok"] = bool(control_sot_ok)
    ident["treatment_sot_ok"] = bool(treatment_sot_ok)
    ident["leftover_ok"] = bool(leftover_ok)
    be_fail = []
    tf1_fail = []
    for t in ctrl_tr:
        key = _tid(t)
        if key is None:
            continue
        h = rows_by.get(key) or {}
        cp = _path(h)
        if cp.get("be_flag_match") is False:
            be_fail.append({"trade_id": _tid_s(key), "be_t": cp.get("be_t"), "source": "u_row"})
        res = residual.get(key) or {}
        if res:
            res_be = bool(res.get("break_even_reached"))
            har_be = bool(cp.get("be_reached"))
            if res_be != har_be:
                be_fail.append(
                    {
                        "trade_id": _tid_s(key),
                        "harvest_be": har_be,
                        "residual_be": res_be,
                        "source": "residual_sot",
                    }
                )
        if cp.get("tf1_ok") is False:
            tf1_fail.append(_tid_s(key))
    ident["be_flag_mismatch_n"] = len(be_fail)
    ident["be_flag_mismatch_sample"] = be_fail[:20]
    ident["tf1_fail_n"] = len(tf1_fail)
    attr = attribution(ctrl_tr, treat_tr)
    direct = float(attr.get("DIRECT_EXIT_DELTA") or 0.0)
    downstream = float(attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)
    displaced = float(attr.get("DISPLACED_TRADE_DELTA") or 0.0)
    total = float(attr.get("TOTAL_CAUSAL_DELTA") or 0.0)
    decomp_ok = abs((direct + downstream + displaced) - total) <= DECOMP_TOL
    ctrl_port = portfolio_pack(ctrl_arm, days)
    treat_port = portfolio_pack(treat_arm, days)
    cm = _tid_map(ctrl_tr)
    tm = _tid_map(treat_tr)
    common = sorted(set(cm) & set(tm))
    treat_only_keys = sorted(set(tm) - set(cm))
    ctrl_only_keys = sorted(set(cm) - set(tm))
    cause_by = {}
    for c in causes:
        key = _tid(c)
        if key is not None:
            cause_by[key] = c
    common_rows: list[dict[str, Any]] = []
    by_path: dict[str, list[dict[str, Any]]] = {p: [] for p in PATH_TYPES}
    by_class: dict[str, list[dict[str, Any]]] = {c: [] for c in RESIDUAL_CLASSES}
    for key in common:
        c = cm[key]
        t = tm[key]
        h = rows_by.get(key) or {}
        cp = _path(h)
        cls = class_of(key, residual, h)
        pth = path_of(key, residual, h)
        dlt = float(t.get("pnl_yen_100") or 0.0) - float(c.get("pnl_yen_100") or 0.0)
        rec = {
            "trade_id": _tid_s(key),
            "date": key[0],
            "symbol": key[1],
            "t0": key[2],
            "fill_role": c.get("fill_role"),
            "path_type": pth,
            "residual_class": cls,
            "ctrl_pnl": float(c.get("pnl_yen_100") or 0.0),
            "treat_pnl": float(t.get("pnl_yen_100") or 0.0),
            "delta": dlt,
            "triggered": bool(cp.get("triggered")) or str(t.get("exit_reason") or "") == CANDIDATE_EXIT_REASON,
            "state_end": cp.get("state_end"),
            "be_reached": cp.get("be_reached"),
            "floor_formed": cp.get("floor_formed"),
            "swing_floor": cp.get("swing_floor"),
            "exit_reason": t.get("exit_reason"),
        }
        common_rows.append(rec)
        by_path.setdefault(pth, []).append(rec)
        by_class.setdefault(cls, []).append(rec)
    path_audit = {p: group_audit(by_path.get(p) or []) for p in PATH_TYPES}
    class_audit = {c: group_audit(by_class.get(c) or []) for c in RESIDUAL_CLASSES}
    fail_rows = (by_class.get("P_EARLY_AFTER_BE") or []) + (by_class.get("P_PROFIT_THEN_FAILURE") or [])
    win_rows = (by_class.get("PROTECTED_GOOD") or []) + (by_class.get("PROTECTED_DIP") or [])
    fail_delta = float(sum(float(r.get("delta") or 0.0) for r in fail_rows))
    win_delta = float(sum(float(r.get("delta") or 0.0) for r in win_rows))
    p_early_delta = float(sum(float(r.get("delta") or 0.0) for r in (by_class.get("P_EARLY_AFTER_BE") or [])))
    ptf_delta = float(sum(float(r.get("delta") or 0.0) for r in (by_class.get("P_PROFIT_THEN_FAILURE") or [])))
    good_delta = float(sum(float(r.get("delta") or 0.0) for r in (by_class.get("PROTECTED_GOOD") or [])))
    dip_delta = float(sum(float(r.get("delta") or 0.0) for r in (by_class.get("PROTECTED_DIP") or [])))
    core_rows = [r for r in common_rows if str(r.get("fill_role") or "") == "CORE"]
    added_rows = [r for r in common_rows if str(r.get("fill_role") or "") == "ADDED"]
    core_win = [r for r in win_rows if str(r.get("fill_role") or "") == "CORE"]
    incr_trades = [tm[k] for k in treat_only_keys]
    incr_econ = arm_econ(incr_trades)
    incr_rows = []
    for k in treat_only_keys:
        t = tm[k]
        cause = dict(cause_by.get(k) or {})
        incr_rows.append(
            {
                "trade_id": _tid_s(k),
                "date": k[0],
                "symbol": k[1],
                "t0": k[2],
                "fill_role": t.get("fill_role"),
                "path_type": t.get("path_type"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "exit_reason": t.get("exit_reason"),
                "slot_release_symbol": cause.get("release_symbol"),
                "slot_release_t0": cause.get("release_t0"),
                "slot_release_reason": cause.get("release_reason"),
                "slot_release_exit_t": cause.get("exit_t"),
                "admit_t": cause.get("admit_t"),
            }
        )
    ctrl_only_rows = [cm[k] for k in ctrl_only_keys]
    days_tbl = day_table(ctrl_tr, treat_tr, days)
    conc = concentration_pack(
        days_tbl,
        ctrl_tr,
        treat_tr,
        common_rows=common_rows,
        treat_only=incr_trades,
        ctrl_only=ctrl_only_rows,
        total=total,
        downstream=downstream,
        displaced=displaced,
        direct=direct,
    )
    return {
        "cohort": cohort,
        "identity": ident,
        "technical_exit_n": int(treat_port.get("technical_exit_n") or 0),
        "common_trigger_n": sum(1 for r in common_rows if bool(r.get("triggered"))),
        "path_audit": path_audit,
        "class_audit": class_audit,
        "FAILURE_DIRECT_DELTA": fail_delta,
        "P_EARLY_DIRECT_DELTA": p_early_delta,
        "PTF_DIRECT_DELTA": ptf_delta,
        "PROTECTED_GOOD_DIP_DIRECT_DELTA": win_delta,
        "GOOD_DIRECT_DELTA": good_delta,
        "DIP_DIRECT_DELTA": dip_delta,
        "CORE_DIRECT_DELTA": float(sum(float(r.get("delta") or 0.0) for r in core_rows)),
        "ADDED_DIRECT_DELTA": float(sum(float(r.get("delta") or 0.0) for r in added_rows)),
        "CORE_WINNER_DIRECT_DELTA": float(sum(float(r.get("delta") or 0.0) for r in core_win)),
        "CORE_WINNER_N": len(core_win),
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": downstream,
        "DISPLACED_TRADE_DELTA": displaced,
        "TOTAL_CAUSAL_DELTA": total,
        "decomp_ok": bool(decomp_ok),
        "winner_harm": {
            "DIP": winner_class_pack(by_class.get("PROTECTED_DIP") or []),
            "GOOD": winner_class_pack(by_class.get("PROTECTED_GOOD") or []),
            "COMBINED_DIRECT_DELTA": win_delta,
        },
        "control": ctrl_port,
        "treatment": treat_port,
        "incremental": {
            "incremental_fill_n": len(incr_trades),
            "incremental_pnl": incr_econ.get("total_pnl"),
            "gross_profit": incr_econ.get("gross_profit"),
            "gross_loss": incr_econ.get("gross_loss"),
            "PF": incr_econ.get("PF"),
            "trades": incr_rows,
        },
        "days": days_tbl,
        "concentration": conc,
        "core_added": {"CORE": group_audit(core_rows), "ADDED": group_audit(added_rows)},
        "common_fills": common_rows,
        "attribution_raw": {
            "COMMON_N": attr.get("COMMON_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
        },
        "fill_n_change": int(treat_port.get("fill_n") or 0) - int(ctrl_port.get("fill_n") or 0),
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    sot_ok = bool(dev.get("identity", {}).get("control_sot_ok")) and bool(fwd.get("identity", {}).get("control_sot_ok"))
    leftover_ok = bool(dev.get("identity", {}).get("leftover_ok")) and bool(fwd.get("identity", {}).get("leftover_ok"))
    decomp_ok = bool(dev.get("decomp_ok")) and bool(fwd.get("decomp_ok"))
    be_ok = int(dev.get("identity", {}).get("be_flag_mismatch_n") or 0) == 0 and int(
        fwd.get("identity", {}).get("be_flag_mismatch_n") or 0
    ) == 0
    tf1_ok = int(dev.get("identity", {}).get("tf1_fail_n") or 0) == 0 and int(fwd.get("identity", {}).get("tf1_fail_n") or 0) == 0
    integrity = bool(leak_ok and harvested_ok and ident_ok and sot_ok and leftover_ok and decomp_ok and be_ok and tf1_ok)
    fail_d = float(dev.get("FAILURE_DIRECT_DELTA") or 0.0)
    win_d = float(dev.get("PROTECTED_GOOD_DIP_DIRECT_DELTA") or 0.0)
    total = float(dev.get("TOTAL_CAUSAL_DELTA") or 0.0)
    c_pf = (dev.get("control") or {}).get("PF")
    t_pf = (dev.get("treatment") or {}).get("PF")
    c_dd = float((dev.get("control") or {}).get("max_drawdown") or 0.0)
    t_dd = float((dev.get("treatment") or {}).get("max_drawdown") or 0.0)
    c_n = int((dev.get("control") or {}).get("fill_n") or 0)
    t_n = int((dev.get("treatment") or {}).get("fill_n") or 0)
    pf_up = _pf_gt(t_pf, c_pf)
    dd_not_worse = t_dd + EPS >= c_dd
    fill_ok = t_n >= c_n
    conc = dict(dev.get("concentration") or {})
    day_ok = not bool(conc.get("single_day_contribution_gt_50pct"))
    sym_ok = not bool(conc.get("single_symbol_contribution_gt_50pct"))
    fwd_total = float(fwd.get("TOTAL_CAUSAL_DELTA") or 0.0)
    fwd_fail = float(fwd.get("FAILURE_DIRECT_DELTA") or 0.0)
    fail_reverse = fail_d > EPS and fwd_fail < -EPS
    burned_total_ok = fwd_total + EPS >= 0.0
    gates = {
        "1_integrity": integrity,
        "2_FAILURE_DIRECT_DELTA_gt_0": fail_d > EPS,
        "3_PROTECTED_GOOD_DIP_DIRECT_DELTA_ge_0": win_d + EPS >= 0.0,
        "4_TOTAL_CAUSAL_DELTA_gt_0": total > EPS,
        "5_treatment_pf_gt_control": pf_up,
        "6_treatment_maxdd_not_worse": dd_not_worse,
        "7_treatment_fill_n_ge_control": fill_ok,
        "8_single_day_contribution_le_50pct": day_ok,
        "9_single_symbol_contribution_le_50pct": sym_ok,
        "burned_total_causal_delta_ge_0": burned_total_ok,
        "burned_failure_direct_not_reversed": not fail_reverse,
    }
    all_dev = all(
        [
            integrity,
            fail_d > EPS,
            win_d + EPS >= 0.0,
            total > EPS,
            pf_up,
            dd_not_worse,
            fill_ok,
            day_ok,
            sym_ok,
        ]
    )
    frozen = False
    family_closed = False
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_POST_BE_SWING_FLOOR_INTEGRITY_FAILED"
        nxt = "FAIL_CLOSED. Control identity, BE arm parity, causal ordering, or occupancy mismatch."
    elif fail_d <= EPS:
        case = "D"
        verdict = "SIMPLE_TECH_POST_BE_SWING_FLOOR_NOT_SUPPORTED"
        nxt = "technical structure itself has no failure separation. family CLOSE. STOP."
        family_closed = True
    elif win_d < -EPS:
        case = "B"
        verdict = "SIMPLE_TECH_POST_BE_SWING_FLOOR_WINNER_HARM"
        nxt = "failure may improve but PROTECTED_GOOD/DIP winners are harmed. family CLOSE. STOP."
        family_closed = True
    elif all_dev and burned_total_ok and (not fail_reverse):
        case = "A"
        verdict = "SIMPLE_TECH_POST_BE_SWING_FLOOR_EXIT_SUPPORTED"
        nxt = (
            f"Freeze {CANDIDATE_ID}. TRUE_OOS=false CERTIFIED=false. Runtime unchanged. "
            "Do not open 20260903/20260904. Do not run prospective harvest in this run."
        )
        frozen = True
    else:
        case = "C"
        verdict = "SIMPLE_TECH_POST_BE_SWING_FLOOR_PORTFOLIO_FAILED"
        reasons = []
        if total <= EPS:
            reasons.append("TOTAL_CAUSAL_DELTA not > 0")
        if not pf_up:
            reasons.append("Treatment PF not greater than Control PF")
        if not dd_not_worse:
            reasons.append("Treatment MaxDD worse than Control")
        if not fill_ok:
            reasons.append("Treatment fill_n < Control fill_n")
        if not day_ok:
            reasons.append("single-day abs contribution >50%")
        if not sym_ok:
            reasons.append("single-symbol abs contribution >50%")
        if not burned_total_ok:
            reasons.append("Burned Stress TOTAL_CAUSAL_DELTA < 0")
        if fail_reverse:
            reasons.append("Burned Stress failure direct delta reversed vs Development")
        nxt = "direct may improve but Full Causal Portfolio / stress failed. family CLOSE. STOP. " + "; ".join(reasons)
        family_closed = True
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(frozen),
        "FAMILY_CLOSED": bool(family_closed),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_EXIT_RULE": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "THRESHOLD_SEARCH": False,
        "TF_SEARCH": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "gates": gates,
        "all_development_gates": all_dev,
    }

"""Full causal portfolio economics for UNPROVEN_EMA_STRUCTURE_LOSS_V1."""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Optional

from research.simple_tech_redesign.branch_u_causal_analyze import _tid_map, attribution
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import (
    EPS,
    _f,
    _pf_gt,
    _tid,
    _tid_s,
    arm_econ,
    day_table,
    identity_check,
    residual_index,
    winner_class_pack,
)
from research.simple_tech_redesign.post_be_causal_swing_floor_exit_v1_analyze import (
    class_of,
    concentration_pack,
    path_of,
    _path,
)
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_spec import (
    CANDIDATE_EXIT_REASON,
    CANDIDATE_ID,
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
from research.simple_tech_strategy.v20_analyze import daily_yen

PATH_SHORT = {
    "GOOD_CONTINUATION": "GOOD",
    "EARLY_FAILURE": "EARLY",
    "DIP_THEN_RECOVERY": "DIP",
    "PROFIT_THEN_FAILURE": "PTF",
    "OTHER": "OTHER",
}


def _pf_ge(treat: Any, ctrl: Any) -> bool:
    from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import _pf_num

    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    return float(ta) + EPS >= float(tb)


def group_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(r.get("delta") or 0.0) for r in rows]
    trig = [r for r in rows if bool(r.get("triggered"))]
    races = Counter(str(r.get("race") or "NEITHER") for r in rows)
    pack = {
        "N": len(rows),
        "EMA_FIRST_N": int(races.get("EMA_FIRST") or 0),
        "BE_FIRST_N": int(races.get("BE_FIRST") or 0),
        "SAME_TIMESTAMP_N": int(races.get("SAME_TIMESTAMP") or 0),
        "TRIGGER_N": len(trig),
        "control_pnl": float(sum(float(r.get("ctrl_pnl") or 0.0) for r in rows)),
        "treatment_pnl": float(sum(float(r.get("treat_pnl") or 0.0) for r in rows)),
        "DIRECT_DELTA": float(sum(deltas)),
        "median_delta": float(median(deltas)) if deltas else None,
        "positive_delta_n": sum(1 for d in deltas if d > EPS),
        "negative_delta_n": sum(1 for d in deltas if d < -EPS),
    }
    return pack


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
    drift = []
    races_all = Counter()
    for t in ctrl_tr:
        key = _tid(t)
        if key is None:
            continue
        h = rows_by.get(key) or {}
        cp = _path(h)
        races_all[str(cp.get("race") or "NEITHER")] += 1
        if cp.get("tf1_ok") is False:
            tf1_fail.append(_tid_s(key))
        if cp.get("predicate_parity_ok") is False:
            drift.append(_tid_s(key))
        res = residual.get(key) or {}
        if res:
            res_be = bool(res.get("break_even_reached"))
            har_be = bool(cp.get("be_reached"))
            if res_be != har_be:
                be_fail.append({"trade_id": _tid_s(key), "harvest_be": har_be, "residual_be": res_be})
    ident["be_flag_mismatch_n"] = len(be_fail)
    ident["be_flag_mismatch_sample"] = be_fail[:20]
    ident["tf1_fail_n"] = len(tf1_fail)
    ident["predicate_drift_n"] = len(drift)
    ident["ok"] = bool(ident.get("ok") and control_sot_ok and treatment_sot_ok and leftover_ok)
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
            "race": cp.get("race"),
            "exit_reason": t.get("exit_reason"),
        }
        common_rows.append(rec)
        by_path.setdefault(pth, []).append(rec)
        by_class.setdefault(cls, []).append(rec)
    path_audit = {p: group_audit(by_path.get(p) or []) for p in PATH_TYPES}
    class_audit = {c: group_audit(by_class.get(c) or []) for c in RESIDUAL_CLASSES}
    u_rows = by_class.get("U_EARLY_NEVER_BE") or []
    pearly_rows = by_class.get("P_EARLY_AFTER_BE") or []
    ptf_rows = by_class.get("P_PROFIT_THEN_FAILURE") or []
    good_rows = by_class.get("PROTECTED_GOOD") or []
    dip_rows = by_class.get("PROTECTED_DIP") or []
    u_delta = float(sum(float(r.get("delta") or 0.0) for r in u_rows))
    pearly_delta = float(sum(float(r.get("delta") or 0.0) for r in pearly_rows))
    ptf_delta = float(sum(float(r.get("delta") or 0.0) for r in ptf_rows))
    good_delta = float(sum(float(r.get("delta") or 0.0) for r in good_rows))
    dip_delta = float(sum(float(r.get("delta") or 0.0) for r in dip_rows))
    win_delta = good_delta + dip_delta
    core_rows = [r for r in common_rows if str(r.get("fill_role") or "") == "CORE"]
    added_rows = [r for r in common_rows if str(r.get("fill_role") or "") == "ADDED"]
    incr_trades = [tm[k] for k in treat_only_keys]
    incr_econ = arm_econ(incr_trades)
    incr_pnls = [float(_f(t.get("pnl_yen_100")) or 0.0) for t in incr_trades]
    incr_paths = Counter(PATH_SHORT.get(str(t.get("path_type") or "OTHER"), "OTHER") for t in incr_trades)
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
    race_common = Counter(str(r.get("race") or "NEITHER") for r in common_rows)
    return {
        "cohort": cohort,
        "identity": ident,
        "technical_exit_n": int(treat_port.get("technical_exit_n") or 0),
        "common_trigger_n": sum(1 for r in common_rows if bool(r.get("triggered"))),
        "EMA_FIRST_N": int(race_common.get("EMA_FIRST") or 0),
        "BE_FIRST_N": int(race_common.get("BE_FIRST") or 0),
        "SAME_TIMESTAMP_N": int(race_common.get("SAME_TIMESTAMP") or 0),
        "occupancy_race": dict(races_all),
        "TIE_RESOLUTION_SOURCE": "exit_lifecycle_harvest._first_bar_flag",
        "path_audit": path_audit,
        "class_audit": class_audit,
        "U_EARLY_NEVER_BE_DIRECT_DELTA": u_delta,
        "P_EARLY_DIRECT_DELTA": pearly_delta,
        "PTF_DIRECT_DELTA": ptf_delta,
        "GOOD_DIRECT_DELTA": good_delta,
        "DIP_DIRECT_DELTA": dip_delta,
        "PROTECTED_KEEP_DIRECT_DELTA": win_delta,
        "CORE_DIRECT_DELTA": float(sum(float(r.get("delta") or 0.0) for r in core_rows)),
        "ADDED_DIRECT_DELTA": float(sum(float(r.get("delta") or 0.0) for r in added_rows)),
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": downstream,
        "DISPLACED_TRADE_DELTA": displaced,
        "TOTAL_CAUSAL_DELTA": total,
        "decomp_ok": bool(decomp_ok),
        "winner_harm": {
            "DIP": winner_class_pack(dip_rows),
            "GOOD": winner_class_pack(good_rows),
            "COMBINED_DIRECT_DELTA": win_delta,
        },
        "control": ctrl_port,
        "treatment": treat_port,
        "incremental": {
            "incremental_fill_n": len(incr_trades),
            "CORE_n": sum(1 for t in incr_trades if str(t.get("fill_role") or "") == "CORE"),
            "ADDED_n": sum(1 for t in incr_trades if str(t.get("fill_role") or "") == "ADDED"),
            "incremental_pnl": incr_econ.get("total_pnl"),
            "gross_profit": incr_econ.get("gross_profit"),
            "gross_loss": incr_econ.get("gross_loss"),
            "PF": incr_econ.get("PF"),
            "win_rate": incr_econ.get("win_rate"),
            "median_pnl": float(median(incr_pnls)) if incr_pnls else None,
            "GOOD": int(incr_paths.get("GOOD") or 0),
            "EARLY": int(incr_paths.get("EARLY") or 0),
            "DIP": int(incr_paths.get("DIP") or 0),
            "PTF": int(incr_paths.get("PTF") or 0),
            "OTHER": int(incr_paths.get("OTHER") or 0),
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


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool, pred_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    sot_ok = (
        bool(dev.get("identity", {}).get("control_sot_ok"))
        and bool(fwd.get("identity", {}).get("control_sot_ok"))
        and bool(dev.get("identity", {}).get("treatment_sot_ok"))
        and bool(fwd.get("identity", {}).get("treatment_sot_ok"))
    )
    leftover_ok = bool(dev.get("identity", {}).get("leftover_ok")) and bool(fwd.get("identity", {}).get("leftover_ok"))
    decomp_ok = bool(dev.get("decomp_ok")) and bool(fwd.get("decomp_ok"))
    be_ok = int(dev.get("identity", {}).get("be_flag_mismatch_n") or 0) == 0 and int(
        fwd.get("identity", {}).get("be_flag_mismatch_n") or 0
    ) == 0
    tf1_ok = int(dev.get("identity", {}).get("tf1_fail_n") or 0) == 0 and int(fwd.get("identity", {}).get("tf1_fail_n") or 0) == 0
    drift_ok = int(dev.get("identity", {}).get("predicate_drift_n") or 0) == 0 and int(
        fwd.get("identity", {}).get("predicate_drift_n") or 0
    ) == 0
    integrity = bool(
        leak_ok and harvested_ok and pred_ok and ident_ok and sot_ok and leftover_ok and decomp_ok and be_ok and tf1_ok and drift_ok
    )
    u_d = float(dev.get("U_EARLY_NEVER_BE_DIRECT_DELTA") or 0.0)
    win_d = float(dev.get("PROTECTED_KEEP_DIRECT_DELTA") or 0.0)
    total = float(dev.get("TOTAL_CAUSAL_DELTA") or 0.0)
    c_pf = (dev.get("control") or {}).get("PF")
    t_pf = (dev.get("treatment") or {}).get("PF")
    c_pnl = float((dev.get("control") or {}).get("total_pnl") or 0.0)
    t_pnl = float((dev.get("treatment") or {}).get("total_pnl") or 0.0)
    c_dd = float((dev.get("control") or {}).get("max_drawdown") or 0.0)
    t_dd = float((dev.get("treatment") or {}).get("max_drawdown") or 0.0)
    pf_up = _pf_gt(t_pf, c_pf)
    pnl_up = t_pnl > c_pnl + EPS
    dd_not_worse = t_dd + EPS >= c_dd
    conc = dict(dev.get("concentration") or {})
    day_ok = not bool(conc.get("single_day_contribution_gt_50pct"))
    sym_ok = not bool(conc.get("single_symbol_contribution_gt_50pct"))
    fwd_u = float(fwd.get("U_EARLY_NEVER_BE_DIRECT_DELTA") or 0.0)
    fwd_win = float(fwd.get("PROTECTED_KEEP_DIRECT_DELTA") or 0.0)
    fwd_total = float(fwd.get("TOTAL_CAUSAL_DELTA") or 0.0)
    fwd_pf_ok = _pf_ge((fwd.get("treatment") or {}).get("PF"), (fwd.get("control") or {}).get("PF"))
    fwd_dd = float((fwd.get("treatment") or {}).get("max_drawdown") or 0.0)
    fwd_cdd = float((fwd.get("control") or {}).get("max_drawdown") or 0.0)
    fwd_dd_ok = fwd_dd + EPS >= fwd_cdd
    fail_reverse = (u_d > EPS and fwd_u < -EPS) or (u_d < -EPS and fwd_u > EPS)
    burned_ok = (
        fwd_u + EPS >= 0.0
        and fwd_win + EPS >= 0.0
        and fwd_total + EPS >= 0.0
        and fwd_pf_ok
        and fwd_dd_ok
        and (not fail_reverse)
    )
    gates = {
        "1_integrity": integrity,
        "2_U_EARLY_NEVER_BE_DIRECT_DELTA_gt_0": u_d > EPS,
        "3_PROTECTED_KEEP_DIRECT_DELTA_ge_0": win_d + EPS >= 0.0,
        "4_TOTAL_CAUSAL_DELTA_gt_0": total > EPS,
        "5_treatment_pf_gt_control": pf_up,
        "6_treatment_maxdd_not_worse": dd_not_worse,
        "7_treatment_pnl_gt_control": pnl_up,
        "8_single_day_contribution_le_50pct": day_ok,
        "9_single_symbol_contribution_le_50pct": sym_ok,
        "burned_u_early_ge_0": fwd_u + EPS >= 0.0,
        "burned_protected_ge_0": fwd_win + EPS >= 0.0,
        "burned_total_ge_0": fwd_total + EPS >= 0.0,
        "burned_pf_not_worse": fwd_pf_ok,
        "burned_maxdd_not_worse": fwd_dd_ok,
        "burned_failure_not_reversed": not fail_reverse,
    }
    all_dev = all(
        [
            integrity,
            u_d > EPS,
            win_d + EPS >= 0.0,
            total > EPS,
            pf_up,
            dd_not_worse,
            pnl_up,
            day_ok,
            sym_ok,
        ]
    )
    frozen = False
    family_closed = False
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_BRANCH_U_EMA_INTEGRITY_FAILED"
        nxt = "FAIL_CLOSED. Exact U_EMA predicate, BE/tie ordering, identity, or timestamp restore failed."
    elif u_d <= EPS:
        case = "D"
        verdict = "SIMPLE_TECH_BRANCH_U_EMA_NOT_SUPPORTED"
        nxt = "U_EARLY_NEVER_BE itself is not improved on common-fill direct delta. U_EMA family CLOSE. STOP. Do not auto-hop to other U primitives this run."
        family_closed = True
    elif win_d < -EPS:
        case = "B"
        verdict = "SIMPLE_TECH_BRANCH_U_EMA_WINNER_HARM"
        nxt = "U failure may improve but GOOD/DIP keep is harmed. U_EMA family CLOSE. STOP. Do not auto-hop to other U primitives this run."
        family_closed = True
    elif all_dev and burned_ok:
        case = "A"
        verdict = "SIMPLE_TECH_BRANCH_U_EMA_EXIT_SUPPORTED"
        nxt = (
            f"Freeze {CANDIDATE_ID}. TRUE_OOS=false CERTIFIED=false. Runtime unchanged. "
            "Do not open 20260903/20260904. Do not run prospective harvest in this run. "
            "Do not auto-hop to other U primitives this run."
        )
        frozen = True
    else:
        case = "C"
        verdict = "SIMPLE_TECH_BRANCH_U_EMA_PORTFOLIO_FAILED"
        nxt = (
            "direct mechanism may improve but Full Causal Portfolio / Burned stress failed. "
            "U_EMA family CLOSE. STOP. Do not auto-hop to other U primitives this run."
        )
        family_closed = True
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(frozen),
        "FAMILY_CLOSED": bool(family_closed),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_CHANGED": False,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "AUTO_HOP": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "gates": gates,
        "all_development_gates": all_dev,
        "burned_direction_ok": burned_ok,
    }

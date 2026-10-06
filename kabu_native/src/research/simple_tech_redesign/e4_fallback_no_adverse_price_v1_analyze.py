"""Full causal portfolio economics for E4 no-adverse-price fallback."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Optional

from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import (
    EPS,
    _f,
    _pf_gt,
    _pf_num,
    arm_econ,
    identity_check,
)
from research.simple_tech_redesign.e4_fallback_no_adverse_price_v1_spec import (
    CANDIDATE_ID,
    CONCENTRATION_MAX_SHARE,
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    PATH_TYPES,
)

PATH_SHORT = {
    "GOOD_CONTINUATION": "GOOD",
    "EARLY_FAILURE": "EARLY",
    "DIP_THEN_RECOVERY": "DIP",
    "PROFIT_THEN_FAILURE": "PTF",
    "OTHER": "OTHER",
}
from research.simple_tech_strategy.v20_analyze import daily_yen


def _pf_ge(treat: Any, ctrl: Any) -> bool:
    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    return float(ta) + EPS >= float(tb)


def group_econ(rows: list[dict[str, Any]], *, pnl_key: str = "ctrl_pnl") -> dict[str, Any]:
    trades = [{"pnl_yen_100": r.get(pnl_key), "exit_time": r.get("t0")} for r in rows]
    econ = arm_econ(trades)
    pnls = [float(_f(r.get(pnl_key)) or 0.0) for r in rows]
    mfes = [float(v) for r in rows if _f(r.get("mfe")) is not None for v in [_f(r.get("mfe"))]]
    maes = [float(v) for r in rows if _f(r.get("mae")) is not None for v in [_f(r.get("mae"))]]
    paths = Counter(str(r.get("path_type") or "OTHER") for r in rows)
    short = Counter(PATH_SHORT.get(str(r.get("path_type") or "OTHER"), "OTHER") for r in rows)
    roles = Counter(str(r.get("fill_role_ctrl") or "") for r in rows)
    econ.update(
        {
            "N": len(rows),
            "CORE_n": int(roles.get("CORE") or 0),
            "ADDED_n": int(roles.get("ADDED") or 0),
            "mean": (sum(pnls) / len(pnls)) if pnls else None,
            "median": float(median(pnls)) if pnls else None,
            "mfe_median": float(median(mfes)) if mfes else None,
            "mae_median": float(median(maes)) if maes else None,
            "path_counts": {p: int(paths.get(p) or 0) for p in PATH_TYPES},
            "GOOD": int(short.get("GOOD") or 0),
            "EARLY": int(short.get("EARLY") or 0),
            "DIP": int(short.get("DIP") or 0),
            "PTF": int(short.get("PTF") or 0),
            "OTHER": int(short.get("OTHER") or 0),
        }
    )
    return econ


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
    econ["fill_n"] = int(arm.get("fill_n") or n)
    econ["core_fill_n"] = sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE")
    econ["added_fill_n"] = sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED")
    econ["CAP_reject_n"] = int(arm.get("cap_blocked") or 0)
    econ["same_symbol_reject_n"] = int(arm.get("same_symbol_blocked") or 0)
    econ["NO_FILL_n"] = int(arm.get("expired_n") or 0)
    econ["slot_release_n"] = int(arm.get("slot_release_n") or 0)
    econ["fills_per_day"] = (n / len(days)) if days else None
    return econ


def concentration(ctrl_tr: list[dict[str, Any]], treat_tr: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    cd = {str(r["date"]): float(r.get("net_pnl") or 0.0) for r in daily_yen(ctrl_tr, days)}
    td = {str(r["date"]): float(r.get("net_pnl") or 0.0) for r in daily_yen(treat_tr, days)}
    day_delta = {d: float(td.get(d) or 0.0) - float(cd.get(d) or 0.0) for d in days}
    def _top(mp: dict[str, float]) -> dict[str, Any]:
        tot = sum(abs(v) for v in mp.values())
        if tot <= EPS:
            return {"top": None, "value": None, "abs_share": None, "warning_gt_50pct": False}
        k = max(mp, key=lambda x: abs(mp[x]))
        share = abs(mp[k]) / tot
        return {"top": k, "value": mp[k], "abs_share": share, "warning_gt_50pct": share > CONCENTRATION_MAX_SHARE + EPS}
    by_sym: dict[str, float] = defaultdict(float)
    cm = {(str(t.get("date")), str(t.get("symbol"))): float(_f(t.get("pnl_yen_100")) or 0.0) for t in ctrl_tr}
    tm = {(str(t.get("date")), str(t.get("symbol"))): float(_f(t.get("pnl_yen_100")) or 0.0) for t in treat_tr}
    keys = set(cm) | set(tm)
    for k in keys:
        by_sym[k[1]] += float(tm.get(k) or 0.0) - float(cm.get(k) or 0.0)
    day_pack = _top(day_delta)
    sym_pack = _top(dict(by_sym))
    return {
        "day": day_pack,
        "symbol": sym_pack,
        "single_day_contribution_gt_50pct": bool(day_pack.get("warning_gt_50pct")),
        "single_symbol_contribution_gt_50pct": bool(sym_pack.get("warning_gt_50pct")),
        "symbol_285A_excluded": False,
    }


def evaluate_cohort(
    *,
    cohort: str,
    days: list[str],
    harvest_rows: list[dict[str, Any]],
    unconstrained: list[dict[str, Any]],
    ctrl_arm: dict[str, Any],
    treat_arm: dict[str, Any],
    core_arm: dict[str, Any],
    leftover_ok: bool,
    control_sot_ok: bool,
    treatment_sot_ok: bool,
    core_only_sot_ok: bool = True,
) -> dict[str, Any]:
    ctrl_tr = list(ctrl_arm.get("trades") or [])
    treat_tr = list(treat_arm.get("trades") or [])
    core_tr = list(core_arm.get("trades") or [])
    if cohort == "DEVELOPMENT":
        ident = identity_check(ctrl_tr, fill_n=DEV_FILL_N, core_n=DEV_CORE_N, added_n=DEV_ADDED_N, pnl=DEV_PNL)
    else:
        ident = identity_check(ctrl_tr, fill_n=FWD_FILL_N, core_n=FWD_CORE_N, added_n=FWD_ADDED_N, pnl=FWD_PNL)
    ident["control_sot_ok"] = bool(control_sot_ok)
    ident["treatment_sot_ok"] = bool(treatment_sot_ok)
    ident["core_only_sot_ok"] = bool(core_only_sot_ok)
    ident["leftover_ok"] = bool(leftover_ok)
    ident["ok"] = bool(ident.get("ok") and control_sot_ok and treatment_sot_ok and core_only_sot_ok and leftover_ok)
    ctrl_port = portfolio_pack(ctrl_arm, days)
    treat_port = portfolio_pack(treat_arm, days)
    core_port = portfolio_pack(core_arm, days)
    core_e4 = [r for r in unconstrained if str(r.get("ctrl_exec_class") or "") == "CORE_E4_FILL"]
    fb_ctrl = [r for r in unconstrained if str(r.get("ctrl_exec_class") or "") == "W5_FALLBACK_CONTROL_FILL"]
    accepted = [r for r in unconstrained if str(r.get("treat_exec_class") or "") == "W5_ACCEPTED_NO_ADVERSE"]
    rejected = [r for r in unconstrained if str(r.get("treat_exec_class") or "") == "W5_REJECTED_ADVERSE_PRICE"]
    degrade = [dict(r.get("price_degrade") or {}) for r in fb_ctrl]
    ctrl_added = int(ctrl_port.get("added_fill_n") or 0)
    treat_added = int(treat_port.get("added_fill_n") or 0)
    core_only_n = int(core_port.get("fill_n") or 0)
    treat_n = int(treat_port.get("fill_n") or 0)
    collapsed = bool(treat_added == 0 or treat_n <= core_only_n)
    total_delta = float(treat_port.get("total_pnl") or 0.0) - float(ctrl_port.get("total_pnl") or 0.0)
    return {
        "cohort": cohort,
        "identity": ident,
        "control": ctrl_port,
        "treatment": treat_port,
        "core_only": core_port,
        "CORE_ONLY_CAUSAL_FILL_N": core_only_n,
        "CONTROL_FILL_N": int(ctrl_port.get("fill_n") or 0),
        "TREATMENT_FILL_N": treat_n,
        "FILL_DELTA": treat_n - int(ctrl_port.get("fill_n") or 0),
        "CONTROL_ADDED_N": ctrl_added,
        "TREATMENT_ADDED_N": treat_added,
        "ADDED_RETENTION": (treat_added / ctrl_added) if ctrl_added else None,
        "COVERAGE_COLLAPSED": collapsed,
        "TOTAL_CAUSAL_DELTA": total_delta,
        "groups": {
            "CORE_E4_FILL": group_econ(core_e4),
            "W5_FALLBACK_CONTROL_FILL": group_econ(fb_ctrl),
            "W5_ACCEPTED_NO_ADVERSE": group_econ(accepted),
            "W5_REJECTED_ADVERSE_PRICE": group_econ(rejected),
        },
        "occupancy_groups": {
            "CONTROL_CORE": group_econ(
                [{"ctrl_pnl": t.get("pnl_yen_100"), "path_type": t.get("path_type"), "fill_role_ctrl": t.get("fill_role"), "mfe": None, "mae": None} for t in ctrl_tr if str(t.get("fill_role") or "") == "CORE"]
            ),
            "CONTROL_ADDED": group_econ(
                [{"ctrl_pnl": t.get("pnl_yen_100"), "path_type": t.get("path_type"), "fill_role_ctrl": t.get("fill_role"), "mfe": None, "mae": None} for t in ctrl_tr if str(t.get("fill_role") or "") == "ADDED"]
            ),
        },
        "price_degradation": {
            "N": len(degrade),
            "median_delta_yen_100": float(median([float(d["delta_yen_100"]) for d in degrade if _f(d.get("delta_yen_100")) is not None])) if any(_f(d.get("delta_yen_100")) is not None for d in degrade) else None,
            "median_delta_ticks": float(median([float(d["delta_ticks"]) for d in degrade if _f(d.get("delta_ticks")) is not None])) if any(_f(d.get("delta_ticks")) is not None for d in degrade) else None,
            "median_delta_bps": float(median([float(d["delta_bps"]) for d in degrade if _f(d.get("delta_bps")) is not None])) if any(_f(d.get("delta_bps")) is not None for d in degrade) else None,
            "adverse_n": sum(1 for d in degrade if _f(d.get("delta_yen_per_share")) is not None and float(d["delta_yen_per_share"]) > EPS),
            "rows": degrade,
        },
        "concentration": concentration(ctrl_tr, treat_tr, days),
        "unconstrained": unconstrained,
        "common_fills": harvest_rows,
        "days": daily_yen(treat_tr, days),
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool, pred_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    integrity = bool(leak_ok and harvested_ok and pred_ok and ident_ok)
    c_pnl = float((dev.get("control") or {}).get("total_pnl") or 0.0)
    t_pnl = float((dev.get("treatment") or {}).get("total_pnl") or 0.0)
    c_pf = (dev.get("control") or {}).get("PF")
    t_pf = (dev.get("treatment") or {}).get("PF")
    c_dd = float((dev.get("control") or {}).get("max_drawdown") or 0.0)
    t_dd = float((dev.get("treatment") or {}).get("max_drawdown") or 0.0)
    pnl_up = t_pnl > c_pnl + EPS
    pf_up = _pf_gt(t_pf, c_pf)
    dd_ok = t_dd + EPS >= c_dd
    core_only = float(dev.get("CORE_ONLY_CAUSAL_FILL_N") or 0.0)
    treat_n = float(dev.get("TREATMENT_FILL_N") or 0.0)
    treat_added = float(dev.get("TREATMENT_ADDED_N") or 0.0)
    cover_ok = treat_n > core_only + EPS
    added_ok = treat_added > EPS
    collapsed = bool(dev.get("COVERAGE_COLLAPSED"))
    conc = dict(dev.get("concentration") or {})
    day_ok = not bool(conc.get("single_day_contribution_gt_50pct"))
    sym_ok = not bool(conc.get("single_symbol_contribution_gt_50pct"))
    fwd_delta = float(fwd.get("TOTAL_CAUSAL_DELTA") or 0.0)
    fwd_pf_ok = _pf_ge((fwd.get("treatment") or {}).get("PF"), (fwd.get("control") or {}).get("PF"))
    fwd_dd = float((fwd.get("treatment") or {}).get("max_drawdown") or 0.0)
    fwd_cdd = float((fwd.get("control") or {}).get("max_drawdown") or 0.0)
    fwd_dd_ok = fwd_dd + EPS >= fwd_cdd
    dev_delta = float(dev.get("TOTAL_CAUSAL_DELTA") or 0.0)
    reverse = (dev_delta > EPS and fwd_delta < -EPS) or (dev_delta < -EPS and fwd_delta > EPS)
    burned_ok = fwd_delta + EPS >= 0.0 and fwd_pf_ok and fwd_dd_ok and (not reverse)
    econ_ok = pnl_up and pf_up and dd_ok
    all_dev = integrity and econ_ok and cover_ok and added_ok and day_ok and sym_ok and (not collapsed)
    gates = {
        "1_integrity": integrity,
        "2_treatment_pnl_gt_control": pnl_up,
        "3_treatment_pf_gt_control": pf_up,
        "4_treatment_maxdd_not_worse": dd_ok,
        "5_treatment_fill_gt_core_only": cover_ok,
        "6_treatment_added_gt_0": added_ok,
        "7_single_day_contribution_le_50pct": day_ok,
        "8_single_symbol_contribution_le_50pct": sym_ok,
        "coverage_collapsed": collapsed,
        "burned_total_ge_0": fwd_delta + EPS >= 0.0,
        "burned_pf_not_worse": fwd_pf_ok,
        "burned_maxdd_not_worse": fwd_dd_ok,
        "burned_not_reversed": not reverse,
    }
    frozen = False
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_INTEGRITY_FAILED"
        nxt = "FAIL_CLOSED. Exact E4 reference restore, identity, or timestamp mismatch."
    elif collapsed and econ_ok:
        case = "C"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_COVERAGE_COLLAPSED"
        nxt = "economics may improve but ADDED coverage collapsed. Candidate freeze forbidden. STOP. Do not hop to P_TREND/P_RCI."
    elif not econ_ok:
        case = "B"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_ECONOMICS_FAILED"
        nxt = "coverage may remain but PnL/PF/MaxDD not improved. Execution candidate CLOSE. STOP. Do not hop to P_TREND/P_RCI."
    elif all_dev and burned_ok:
        case = "A"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_SUPPORTED"
        nxt = (
            f"Freeze {CANDIDATE_ID}. TRUE_OOS=false CERTIFIED=false. Runtime unchanged. "
            "Do not open 20260903/20260904. Do not run prospective harvest in this run."
        )
        frozen = True
    elif all_dev and not burned_ok:
        case = "D"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_BURNED_FAILED"
        nxt = "DEV support but Burned direction mismatch. Candidate freeze forbidden. STOP. Do not change the rule from Burned."
    else:
        case = "B"
        verdict = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_ECONOMICS_FAILED"
        nxt = "DEV gates incomplete (coverage/concentration). Execution candidate CLOSE. STOP."
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(frozen),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_CHANGED": True,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "AUTO_HOP": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "gates": gates,
        "all_development_gates": all_dev,
        "burned_direction_ok": burned_ok,
        "COVERAGE_COLLAPSED": collapsed,
    }

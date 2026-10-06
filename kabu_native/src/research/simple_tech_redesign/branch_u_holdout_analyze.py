"""Holdout occupancy economics. Frozen Branch U. No date cherry-pick. No new EXIT."""
from __future__ import annotations

from typing import Any

from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_redesign.branch_u_bb_analyze import _public_econ
from research.simple_tech_redesign.branch_u_bb_spec import EXIT_REASON, TESTED_BRANCH_U_EXIT_MECHANISMS
from research.simple_tech_redesign.branch_u_causal_analyze import _as_arm_fills, _counts, _tid_map, attribution
from research.simple_tech_redesign.branch_u_causal_harvest import _sym
from research.simple_tech_redesign.branch_u_holdout_spec import (
    DIP_PATH,
    EARLY_PATH,
    GOOD_PATH,
    HOLDOUT_CLASS,
    MIN_BRANCH_U_EXIT_N,
    OTHER_PATH,
    PATH_TYPES,
    PTF_PATH,
)
from research.simple_tech_redesign.v28_analyze import _arm_rows, _delta, _pf_num
from research.simple_tech_strategy.v20_analyze import concentration_yen, daily_yen, pnl_pack


def economics_days(fills: list[dict[str, Any]], arm: str, days: list[str]) -> dict[str, Any]:
    rows = _arm_rows(fills, arm)
    use_days = list(days)
    pnl = pnl_pack(rows, use_days)
    conc = concentration_yen(rows, use_days)
    miss_n = sum(1 for r in rows if r.get("exit_miss"))
    return {
        "TRADE_N": len(rows),
        "EXIT_MISS_N": int(miss_n),
        "TOTAL_PNL_YEN": pnl.get("TOTAL_PNL_YEN_100"),
        "AVG_PNL_YEN": pnl.get("AVG_PNL_PER_TRADE"),
        "MEDIAN_PNL_YEN": pnl.get("MEDIAN_PNL_PER_TRADE"),
        "WIN_RATE": pnl.get("WIN_RATE"),
        "GROSS_PROFIT": pnl.get("GROSS_PROFIT"),
        "GROSS_LOSS": pnl.get("GROSS_LOSS"),
        "PF": pnl.get("PROFIT_FACTOR"),
        "REALIZED_MAX_DD": pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"),
        "POSITIVE_DAY_N": conc.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": conc.get("NEGATIVE_DAY_N"),
        "BEST_DAY": conc.get("BEST_DAY"),
        "BEST_DAY_PNL": conc.get("BEST_DAY_PNL"),
        "WORST_DAY_PNL": conc.get("WORST_DAY_PNL"),
        "EX_BEST_DAY_TOTAL_PNL": conc.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL": conc.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "LODO_MIN_TOTAL_PNL": conc.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MEDIAN_TOTAL_PNL": conc.get("LODO_MEDIAN_TOTAL_PNL"),
        "daily": conc.get("daily"),
        "WIN_N": pnl.get("WIN_N"),
        "LOSS_N": pnl.get("LOSS_N"),
    }


def econ(trades: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    return economics_days(_as_arm_fills(trades), "control_exit", days)


def path_u_counts(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by = {p: 0 for p in PATH_TYPES}
    n = 0
    for t in trades:
        if str(t.get("exit_reason") or "") != EXIT_REASON:
            continue
        n += 1
        p = str(t.get("path_type") or OTHER_PATH)
        if p not in by:
            p = OTHER_PATH
        by[p] += 1
    return {
        "BRANCH_U_EXIT_N": int(n),
        "GOOD_BRANCH_U_EXIT_N": int(by.get(GOOD_PATH) or 0),
        "DIP_BRANCH_U_EXIT_N": int(by.get(DIP_PATH) or 0),
        "EARLY_BRANCH_U_EXIT_N": int(by.get(EARLY_PATH) or 0),
        "PTF_BRANCH_U_EXIT_N": int(by.get(PTF_PATH) or 0),
        "OTHER_BRANCH_U_EXIT_N": int(by.get(OTHER_PATH) or 0),
        "by_path": by,
    }


def unconstrained_u_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fake = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        fake.append(
            {
                "exit_reason": str((r.get("treatment_exit") or {}).get("reason") or ""),
                "path_type": r.get("path_type"),
            }
        )
    return path_u_counts(fake)


def day_table(
    ctrl_trades: list[dict[str, Any]],
    treat_trades: list[dict[str, Any]],
    days: list[str],
    day_bodies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    c_daily = {str(r.get("date") or ""): r for r in daily_yen(ctrl_trades, list(days))}
    t_daily = {str(r.get("date") or ""): r for r in daily_yen(treat_trades, list(days))}
    treat_by_day: dict[str, list[dict[str, Any]]] = {}
    ctrl_ids = set(_tid_map(ctrl_trades))
    for t in treat_trades:
        treat_by_day.setdefault(str(t.get("date") or ""), []).append(t)
    by_date = {str(b.get("date") or ""): b for b in day_bodies}
    rows = []
    for day in list(days):
        c = dict(c_daily.get(day) or {})
        t = dict(t_daily.get(day) or {})
        c_pnl = float(c.get("net_pnl") or 0.0)
        t_pnl = float(t.get("net_pnl") or 0.0)
        delta = t_pnl - c_pnl
        if delta > 1e-12:
            side = "IMPROVE"
        elif delta < -1e-12:
            side = "WORSEN"
        else:
            side = "FLAT"
        day_tr = list(treat_by_day.get(day) or [])
        add_n = 0
        for tr in day_tr:
            t0 = tr.get("t0")
            if not _finite(t0):
                continue
            if (day, _sym(tr), float(t0)) not in ctrl_ids:
                add_n += 1
        body = dict(by_date.get(day) or {})
        cap_c = int((body.get("control") or {}).get("cap_blocked") or 0)
        cap_t = int((body.get("treatment") or {}).get("cap_blocked") or 0)
        day_ctrl = [x for x in ctrl_trades if str(x.get("date") or "") == day]
        day_treat = [x for x in treat_trades if str(x.get("date") or "") == day]
        day_attr = attribution(day_ctrl, day_treat)
        rows.append(
            {
                "date": day,
                "control_pnl": c_pnl,
                "treatment_pnl": t_pnl,
                "delta_pnl": delta,
                "additional_fill_n": int(add_n),
                "branch_u_exit_n": sum(1 for tr in day_tr if str(tr.get("exit_reason") or "") == EXIT_REASON),
                "control_fill_n": int(c.get("trade_n") or 0),
                "treatment_fill_n": int(t.get("trade_n") or 0),
                "control_cap_reject_n": cap_c,
                "treatment_cap_reject_n": cap_t,
                "cap_reject_delta": cap_t - cap_c,
                "direct_exit_delta": day_attr.get("DIRECT_EXIT_DELTA"),
                "downstream_delta": day_attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
                "displaced_delta": day_attr.get("DISPLACED_TRADE_DELTA"),
                "side": side,
            }
        )
    return rows


def _attach_day(day_bodies: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    out = []
    for day, body in zip(days, day_bodies):
        rec = dict(body)
        rec["date"] = day
        out.append(rec)
    return out


def summarize(
    rows: list[dict[str, Any]],
    *,
    days: list[str],
    ctrl: dict[str, Any],
    treat: dict[str, Any],
    day_bodies: list[dict[str, Any]],
    provenance: list[dict[str, Any]],
    parity: dict[str, Any],
) -> dict[str, Any]:
    tagged = _attach_day(day_bodies, days)
    ctrl_tr = list(ctrl.get("trades") or [])
    treat_tr = list(treat.get("trades") or [])
    attr = attribution(ctrl_tr, treat_tr)
    occ_u = path_u_counts(treat_tr)
    unconst_u = unconstrained_u_counts(rows)
    c_econ = econ(ctrl_tr, days)
    t_econ = econ(treat_tr, days)
    core_c = econ([x for x in ctrl_tr if str(x.get("fill_role") or "") == "CORE"], days)
    core_t = econ([x for x in treat_tr if str(x.get("fill_role") or "") == "CORE"], days)
    add_c = econ([x for x in ctrl_tr if str(x.get("fill_role") or "") == "ADDED"], days)
    add_t = econ([x for x in treat_tr if str(x.get("fill_role") or "") == "ADDED"], days)
    cm = _tid_map(ctrl_tr)
    tm = _tid_map(treat_tr)
    common_c = [cm[k] for k in sorted(set(cm) & set(tm))]
    common_t = [tm[k] for k in sorted(set(cm) & set(tm))]
    incr = [tm[k] for k in sorted(set(tm) - set(cm))]
    days_tbl = day_table(ctrl_tr, treat_tr, days, tagged)
    improve = sum(1 for r in days_tbl if r["side"] == "IMPROVE")
    worsen = sum(1 for r in days_tbl if r["side"] == "WORSEN")
    flat = sum(1 for r in days_tbl if r["side"] == "FLAT")
    fills_unconst = [r for r in rows if r.get("actual_filled")]
    return {
        "HOLDOUT_DAYS": list(days),
        "HOLDOUT_DAY_N": len(days),
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "UNCONSTRAINED_FILL_N": len(fills_unconst),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
        "CONTROL_COUNTS": _counts(ctrl),
        "TREATMENT_COUNTS": _counts(treat),
        "incremental_trade_n": attr.get("INCREMENTAL_TREATMENT_N"),
        "control_only_trade_n": attr.get("CONTROL_ONLY_N"),
        "CONTROL_ECONOMICS": _public_econ(c_econ),
        "TREATMENT_ECONOMICS": _public_econ(t_econ),
        "CORE_CONTROL_ECONOMICS": _public_econ(core_c),
        "CORE_TREATMENT_ECONOMICS": _public_econ(core_t),
        "ADDED_CONTROL_ECONOMICS": _public_econ(add_c),
        "ADDED_TREATMENT_ECONOMICS": _public_econ(add_t),
        "COMMON_CONTROL_ECONOMICS": _public_econ(econ(common_c, days)),
        "COMMON_TREATMENT_ECONOMICS": _public_econ(econ(common_t, days)),
        "INCREMENTAL_ECONOMICS": _public_econ(econ(incr, days)),
        "ATTRIBUTION": {
            "DIRECT_EXIT_DELTA": attr.get("DIRECT_EXIT_DELTA"),
            "SLOT_RELEASE_DOWNSTREAM_DELTA": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "DISPLACED_TRADE_DELTA": attr.get("DISPLACED_TRADE_DELTA"),
            "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
            "COMMON_N": attr.get("COMMON_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
        },
        "WINNER_SAFETY_OCCUPANCY": occ_u,
        "WINNER_SAFETY_UNCONSTRAINED": unconst_u,
        "DAY_ROBUSTNESS": {
            "IMPROVE_DAY_N": int(improve),
            "WORSEN_DAY_N": int(worsen),
            "FLAT_DAY_N": int(flat),
            "daily": days_tbl,
        },
        "PROVENANCE": provenance,
        "PARITY": parity,
        "DELTA_TOTAL_PNL": _delta(t_econ, c_econ, "TOTAL_PNL_YEN"),
        "DELTA_PF": _delta(t_econ, c_econ, "PF"),
        "DELTA_MAX_DD": _delta(t_econ, c_econ, "REALIZED_MAX_DD"),
        "trades_control": ctrl_tr,
        "trades_treatment": treat_tr,
    }


def _pf_ge(treat: Any, ctrl: Any) -> bool:
    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    if ta == float("inf") and tb == float("inf"):
        return True
    if ta == float("inf"):
        return True
    if tb == float("inf"):
        return False
    return float(ta) + 1e-12 >= float(tb)


def decide(
    summary: dict[str, Any],
    *,
    leak_ok: bool,
    ni_ok: bool,
    occupancy_ok: bool,
    pre_div_ok: bool,
    leftover_ok: bool,
    cherry_ok: bool,
    today_excluded: bool,
) -> dict[str, Any]:
    tested = list(summary.get("TESTED_BRANCH_U_EXIT_MECHANISMS") or [])
    tested_ok = tested == ["U_BB_LOWER_BREAK"]
    integrity = bool(
        leak_ok
        and ni_ok
        and occupancy_ok
        and pre_div_ok
        and leftover_ok
        and cherry_ok
        and today_excluded
        and tested_ok
    )
    occ = dict(summary.get("WINNER_SAFETY_OCCUPANCY") or {})
    u_n = int(occ.get("BRANCH_U_EXIT_N") or 0)
    good_n = int(occ.get("GOOD_BRANCH_U_EXIT_N") or 0)
    dip_n = int(occ.get("DIP_BRANCH_U_EXIT_N") or 0)
    attr = dict(summary.get("ATTRIBUTION") or {})
    d_pnl = summary.get("DELTA_TOTAL_PNL")
    d_pf = summary.get("DELTA_PF")
    d_dd = summary.get("DELTA_MAX_DD")
    direct = attr.get("DIRECT_EXIT_DELTA")
    down = attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA")
    te = dict(summary.get("TREATMENT_ECONOMICS") or {})
    ce = dict(summary.get("CONTROL_ECONOMICS") or {})
    total_up = _finite(d_pnl) and float(d_pnl) > 1e-12
    pf_ok = _pf_ge(te.get("PF"), ce.get("PF"))
    dd_ok = _finite(d_dd) and float(d_dd) >= -1e-12
    direct_ok = _finite(direct) and float(direct) >= -1e-12
    down_f = float(down or 0.0)
    direct_f = float(direct or 0.0)
    if direct_f > 1e-12:
        down_ok = down_f >= -0.5 * direct_f
    else:
        down_ok = down_f >= -1e-12
    winner_ok = good_n == 0 and dip_n == 0
    freeze = (
        "Do not retune BB. Do not add Branch P / PTF / EARLY EXIT. "
        "Do not change sizing. TRUE_OOS=false. CERTIFIED=false."
    )
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_BRANCH_U_HOLDOUT_INVALID"
        next_step = "STOP. Integrity failed. Do not treat this as holdout evidence. " + freeze
    elif u_n < int(MIN_BRANCH_U_EXIT_N):
        case = "D"
        verdict = "SIMPLE_TECH_BRANCH_U_HOLDOUT_ACCUMULATING"
        next_step = (
            f"STOP. BRANCH_U_EXIT_N={u_n} < {int(MIN_BRANCH_U_EXIT_N)}. "
            "Keep Branch U frozen. Append the next complete capture only. " + freeze
        )
    elif (
        total_up
        and pf_ok
        and dd_ok
        and direct_ok
        and down_ok
        and winner_ok
    ):
        case = "A"
        verdict = "SIMPLE_TECH_BRANCH_U_TEMPORAL_HOLDOUT_SUPPORTED"
        next_step = "Next is Exact Runtime-path parity. Do not size yet. " + freeze
    else:
        case = "C"
        verdict = "SIMPLE_TECH_BRANCH_U_TEMPORAL_HOLDOUT_CONTRADICTED"
        next_step = "STOP. Do not retune BB. Next is Development vs Holdout RCA. " + freeze
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "HOLDOUT_CLASS": HOLDOUT_CLASS,
        "TESTED_BRANCH_U_EXIT_MECHANISMS": tested,
        "FORCE_TREATMENT_FILL_SET": False,
        "OCCUPANCY_SOT_PARITY": occupancy_ok,
        "PRE_DIVERGENCE_PARITY": pre_div_ok,
        "BRANCH_U_EXIT_N": u_n,
        "MIN_BRANCH_U_EXIT_N": int(MIN_BRANCH_U_EXIT_N),
        "GOOD_BRANCH_U_EXIT_N": good_n,
        "DIP_BRANCH_U_EXIT_N": dip_n,
        "EARLY_BRANCH_U_EXIT_N": occ.get("EARLY_BRANCH_U_EXIT_N"),
        "PTF_BRANCH_U_EXIT_N": occ.get("PTF_BRANCH_U_EXIT_N"),
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": down,
        "DISPLACED_TRADE_DELTA": attr.get("DISPLACED_TRADE_DELTA"),
        "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
        "DELTA_TOTAL_PNL": d_pnl,
        "DELTA_PF": d_pf,
        "DELTA_MAX_DD": d_dd,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "BRANCH_P_ADDED": False,
        "V1R_PROCESS_MARKET_PUSH_USED": False,
        "DATE_CHERRY_PICK": False,
    }

"""Branch U one-shot economics vs frozen session-close control. No alternate mechanism ranking."""
from __future__ import annotations

from typing import Any

from replay.pnl_yen import summarize_pnl_yen_100
from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_redesign.branch_u_bb_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    DIP_PATH,
    EARLY_PATH,
    EXIT_REASON,
    GOOD_PATH,
    OTHER_PATH,
    PATH_TYPES,
    PTF_PATH,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    YEN_PARITY_TOL,
)
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v28_analyze import _arm_rows, _delta, _fills, economics


def _close_yen(a: Any, b: Any) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(YEN_PARITY_TOL)


def _trade_id(r: dict[str, Any]) -> str:
    return f"{r.get('date')}|{str(r.get('symbol') or '').replace('.T', '')}|{r.get('t0')}"


def _is_u_exit(r: dict[str, Any]) -> bool:
    return str((r.get("treatment_exit") or {}).get("reason") or "") == EXIT_REASON


def path_economics(fills: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ptype in PATH_TYPES:
        grp = [r for r in fills if str(r.get("path_type") or "") == ptype]
        u_n = sum(1 for r in grp if _is_u_exit(r))
        c = summarize_pnl_yen_100(_arm_rows(grp, "control_exit"))
        t = summarize_pnl_yen_100(_arm_rows(grp, "treatment_exit"))
        cp = c.get("total_pnl_yen_100")
        tp = t.get("total_pnl_yen_100")
        out[ptype] = {
            "n": len(grp),
            "BRANCH_U_EXIT_N": int(u_n),
            "control_pnl": cp,
            "treatment_pnl": tp,
            "delta_pnl": (float(tp or 0.0) - float(cp or 0.0)) if grp else None,
        }
    return out


def day_robustness(fills: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = economics(fills, "control_exit")
    treat = economics(fills, "treatment_exit")
    c_daily = {str(r.get("date") or ""): r for r in list(ctrl.get("daily") or [])}
    t_daily = {str(r.get("date") or ""): r for r in list(treat.get("daily") or [])}
    rows = []
    improve = worsen = flat = 0
    for day in list(ELIGIBLE_DAYS):
        c = dict(c_daily.get(day) or {})
        t = dict(t_daily.get(day) or {})
        c_pnl = float(c.get("net_pnl") or 0.0)
        t_pnl = float(t.get("net_pnl") or 0.0)
        delta = t_pnl - c_pnl
        if delta > 1e-12:
            improve += 1
            side = "IMPROVE"
        elif delta < -1e-12:
            worsen += 1
            side = "WORSEN"
        else:
            flat += 1
            side = "FLAT"
        rows.append(
            {
                "date": day,
                "control_pnl": c_pnl,
                "treatment_pnl": t_pnl,
                "delta_pnl": delta,
                "control_trade_n": c.get("trade_n"),
                "treatment_trade_n": t.get("trade_n"),
                "side": side,
            }
        )
    return {
        "daily": rows,
        "IMPROVE_DAY_N": int(improve),
        "WORSEN_DAY_N": int(worsen),
        "FLAT_DAY_N": int(flat),
        "EX_BEST_DAY_TOTAL_PNL_CONTROL": ctrl.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_BEST_DAY_TOTAL_PNL_TREATMENT": treat.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL_CONTROL": ctrl.get("EX_TOP3_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL_TREATMENT": treat.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL_CONTROL": ctrl.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL_TREATMENT": treat.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "LODO_MIN_CONTROL": ctrl.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MIN_TREATMENT": treat.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MEDIAN_CONTROL": ctrl.get("LODO_MEDIAN_TOTAL_PNL"),
        "LODO_MEDIAN_TREATMENT": treat.get("LODO_MEDIAN_TOTAL_PNL"),
    }


def control_unique(fills: list[dict[str, Any]]) -> bool:
    if not fills:
        return False
    for r in fills:
        c = dict(r.get("control_exit") or {})
        if str(c.get("reason") or "") != "SESSION_CLOSE":
            return False
        if bool(c.get("miss")):
            return False
        if not _finite(c.get("exit_bid")) or not _finite(c.get("exit_t")):
            return False
    return True


def audit_rows(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in fills:
        u = dict(r.get("branch_u") or {})
        c = dict(r.get("control_exit") or {})
        t = dict(r.get("treatment_exit") or {})
        cp = c.get("pnl_yen_100")
        tp = t.get("pnl_yen_100")
        delta = None
        if _finite(cp) and _finite(tp):
            delta = float(tp) - float(cp)
        out.append(
            {
                "trade_id": _trade_id(r),
                "fill_role": r.get("fill_role"),
                "path_type": r.get("path_type"),
                "date": r.get("date"),
                "symbol": str(r.get("symbol") or "").replace(".T", ""),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "lifecycle_state": u.get("lifecycle_state"),
                "break_even_reached": u.get("break_even_reached"),
                "first_break_even_time": u.get("first_break_even_time"),
                "u_bb_lower_break_time": u.get("u_bb_lower_break_time"),
                "bb_break_before_be": u.get("bb_break_before_be"),
                "trigger_time": t.get("trigger_event_time") if t.get("trigger_event_time") is not None else u.get("trigger_time"),
                "trigger_bar_time": t.get("trigger_bar_time") if t.get("trigger_bar_time") is not None else u.get("trigger_bar_time"),
                "exit_bid_time": t.get("exit_t"),
                "exit_price": t.get("exit_bid"),
                "treatment_reason": t.get("reason"),
                "control_reason": c.get("reason"),
                "control_exit_t": c.get("exit_t"),
                "control_exit_bid": c.get("exit_bid"),
                "control_pnl": cp,
                "treatment_pnl": tp,
                "paired_pnl_delta": delta,
                "branch_u_exit_triggered": u.get("branch_u_exit_triggered"),
                "freshness_evidence": t.get("freshness_evidence"),
                "exit_event_sequence": t.get("exit_event_sequence"),
            }
        )
    return out


def _public_econ(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "TRADE_N": pack.get("TRADE_N"),
        "EXIT_MISS_N": pack.get("EXIT_MISS_N"),
        "TOTAL_PNL_YEN": pack.get("TOTAL_PNL_YEN"),
        "AVG_PNL_YEN": pack.get("AVG_PNL_YEN"),
        "MEDIAN_PNL_YEN": pack.get("MEDIAN_PNL_YEN"),
        "WIN_RATE": pack.get("WIN_RATE"),
        "GROSS_PROFIT": pack.get("GROSS_PROFIT"),
        "GROSS_LOSS": pack.get("GROSS_LOSS"),
        "PF": pack.get("PF"),
        "REALIZED_MAX_DD": pack.get("REALIZED_MAX_DD"),
        "POSITIVE_DAY_N": pack.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": pack.get("NEGATIVE_DAY_N"),
        "BEST_DAY": pack.get("BEST_DAY"),
        "BEST_DAY_PNL": pack.get("BEST_DAY_PNL"),
        "WORST_DAY_PNL": pack.get("WORST_DAY_PNL"),
        "EX_BEST_DAY_TOTAL_PNL": pack.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL": pack.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL": pack.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "LODO_MIN_TOTAL_PNL": pack.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MEDIAN_TOTAL_PNL": pack.get("LODO_MEDIAN_TOTAL_PNL"),
        "WIN_N": pack.get("WIN_N"),
        "LOSS_N": pack.get("LOSS_N"),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = _fills(rows)
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    overall_ctrl = economics(fills, "control_exit")
    overall_treat = economics(fills, "treatment_exit")
    core_ctrl = economics(core, "control_exit")
    core_treat = economics(core, "treatment_exit")
    added_ctrl = economics(added, "control_exit")
    added_treat = economics(added, "treatment_exit")
    u_exits = [r for r in fills if _is_u_exit(r)]
    sess = [r for r in fills if not _is_u_exit(r)]
    dip_u = [r for r in fills if str(r.get("path_type") or "") == DIP_PATH and _is_u_exit(r)]
    good_u = [r for r in fills if str(r.get("path_type") or "") == GOOD_PATH and _is_u_exit(r)]
    early_u = [r for r in fills if str(r.get("path_type") or "") == EARLY_PATH and _is_u_exit(r)]
    path = path_economics(fills)
    days_pack = day_robustness(fills)
    tested = list(TESTED_BRANCH_U_EXIT_MECHANISMS)
    control_ok = control_unique(fills)
    proven_u = 0
    for r in fills:
        u = dict(r.get("branch_u") or {})
        if str(u.get("lifecycle_state") or "") == "PROVEN" and _is_u_exit(r):
            proven_u += 1
    deltas = {
        "DELTA_TOTAL_PNL": _delta(overall_treat, overall_ctrl, "TOTAL_PNL_YEN"),
        "DELTA_PF": _delta(overall_treat, overall_ctrl, "PF"),
        "DELTA_MAX_DD": _delta(overall_treat, overall_ctrl, "REALIZED_MAX_DD"),
        "DELTA_EX_BEST_DAY": _delta(overall_treat, overall_ctrl, "EX_BEST_DAY_TOTAL_PNL"),
        "DELTA_EX_TOP3_DAY": _delta(overall_treat, overall_ctrl, "EX_TOP3_DAY_TOTAL_PNL"),
        "DELTA_DROP_TOP_SYMBOL": _delta(overall_treat, overall_ctrl, "DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DELTA_LODO_MIN": _delta(overall_treat, overall_ctrl, "LODO_MIN_TOTAL_PNL"),
        "DELTA_LODO_MEDIAN": _delta(overall_treat, overall_ctrl, "LODO_MEDIAN_TOTAL_PNL"),
    }
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "CORE_FILL_N": len(core),
        "ADDED_FILL_N": len(added),
        "TOTAL_RESEARCH_FILL_N": len(fills),
        "CORRECTED_FILL_HASH": set_hash(fill_tuples_e4(rows)),
        "RESEARCH_FILL_SET_HASH": set_hash(research_fill_tuples(rows)),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": tested,
        "CONTROL_UNIQUE": bool(control_ok),
        "BRANCH_U_EXIT_N": len(u_exits),
        "SESSION_CLOSE_EXIT_N": len(sess),
        "BRANCH_U_CORE_EXIT_N": sum(1 for r in core if _is_u_exit(r)),
        "BRANCH_U_ADDED_EXIT_N": sum(1 for r in added if _is_u_exit(r)),
        "DIP_BRANCH_U_EXIT_N": len(dip_u),
        "GOOD_BRANCH_U_EXIT_N": len(good_u),
        "EARLY_BRANCH_U_EXIT_N": len(early_u),
        "U_EXIT_AFTER_PROVEN_N": int(proven_u),
        "OVERALL_CONTROL_ECONOMICS": _public_econ(overall_ctrl),
        "OVERALL_TREATMENT_ECONOMICS": _public_econ(overall_treat),
        "CORE_CONTROL_ECONOMICS": _public_econ(core_ctrl),
        "CORE_TREATMENT_ECONOMICS": _public_econ(core_treat),
        "ADDED_CONTROL_ECONOMICS": _public_econ(added_ctrl),
        "ADDED_TREATMENT_ECONOMICS": _public_econ(added_treat),
        "PATH_ECONOMICS": path,
        "DAY_ROBUSTNESS": {
            "IMPROVE_DAY_N": days_pack.get("IMPROVE_DAY_N"),
            "WORSEN_DAY_N": days_pack.get("WORSEN_DAY_N"),
            "FLAT_DAY_N": days_pack.get("FLAT_DAY_N"),
            "daily": days_pack.get("daily"),
        },
        **deltas,
        "trades": audit_rows(fills),
        "concentration_control": {
            "EX_BEST_DAY_TOTAL_PNL": overall_ctrl.get("EX_BEST_DAY_TOTAL_PNL"),
            "EX_TOP3_DAY_TOTAL_PNL": overall_ctrl.get("EX_TOP3_DAY_TOTAL_PNL"),
            "DROP_TOP_SYMBOL_TOTAL_PNL": overall_ctrl.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
            "LODO_MIN_TOTAL_PNL": overall_ctrl.get("LODO_MIN_TOTAL_PNL"),
            "LODO_MEDIAN_TOTAL_PNL": overall_ctrl.get("LODO_MEDIAN_TOTAL_PNL"),
        },
        "concentration_treatment": {
            "EX_BEST_DAY_TOTAL_PNL": overall_treat.get("EX_BEST_DAY_TOTAL_PNL"),
            "EX_TOP3_DAY_TOTAL_PNL": overall_treat.get("EX_TOP3_DAY_TOTAL_PNL"),
            "DROP_TOP_SYMBOL_TOTAL_PNL": overall_treat.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
            "LODO_MIN_TOTAL_PNL": overall_treat.get("LODO_MIN_TOTAL_PNL"),
            "LODO_MEDIAN_TOTAL_PNL": overall_treat.get("LODO_MEDIAN_TOTAL_PNL"),
        },
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    fill_identity: bool,
    leak_ok: bool,
    ni_ok: bool,
    exit_miss_ok: bool,
) -> dict[str, Any]:
    tested = list(summary.get("TESTED_BRANCH_U_EXIT_MECHANISMS") or [])
    tested_ok = tested == ["U_BB_LOWER_BREAK"]
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("CORE_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
        and int(summary.get("ADDED_FILL_N") or 0) == int(ADDED_FILL_N_EXPECTED)
        and int(summary.get("TOTAL_RESEARCH_FILL_N") or 0) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
    )
    hash_ok = (
        str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
        and str(summary.get("RESEARCH_FILL_SET_HASH") or "") == RESEARCH_FILL_SET_HASH_EXPECTED
        and bool(fill_identity)
    )
    control_ok = bool(summary.get("CONTROL_UNIQUE"))
    proven_u_ok = int(summary.get("U_EXIT_AFTER_PROVEN_N") or 0) == 0
    integrity = bool(
        leak_ok
        and ni_ok
        and signal_parity
        and counts_ok
        and hash_ok
        and tested_ok
        and control_ok
        and proven_u_ok
        and exit_miss_ok
    )
    path = dict(summary.get("PATH_ECONOMICS") or {})
    early = dict(path.get(EARLY_PATH) or {})
    dip = dict(path.get(DIP_PATH) or {})
    good = dict(path.get(GOOD_PATH) or {})
    ptf = dict(path.get(PTF_PATH) or {})
    other = dict(path.get(OTHER_PATH) or {})
    d_pnl = summary.get("DELTA_TOTAL_PNL")
    d_pf = summary.get("DELTA_PF")
    d_dd = summary.get("DELTA_MAX_DD")
    d_ex = summary.get("DELTA_EX_BEST_DAY")
    d_top3 = summary.get("DELTA_EX_TOP3_DAY")
    early_d = early.get("delta_pnl")
    dip_d = dip.get("delta_pnl")
    good_d = good.get("delta_pnl")
    dip_u_n = int(summary.get("DIP_BRANCH_U_EXIT_N") or 0)
    good_u_n = int(summary.get("GOOD_BRANCH_U_EXIT_N") or 0)
    day = dict(summary.get("DAY_ROBUSTNESS") or {})
    improve_n = int(day.get("IMPROVE_DAY_N") or 0)
    worsen_n = int(day.get("WORSEN_DAY_N") or 0)
    early_reduced = _finite(early_d) and float(early_d) > 1e-12
    dip_ok = (not _finite(dip_d)) or float(dip_d) >= -1e-12
    good_ok = (not _finite(good_d)) or float(good_d) >= -1e-12
    dip_harm = _finite(dip_d) and float(dip_d) < -1e-12
    good_harm = _finite(good_d) and float(good_d) < -1e-12
    winner_harm = bool(dip_harm or good_harm)
    total_up = _finite(d_pnl) and float(d_pnl) > 1e-12
    total_down = _finite(d_pnl) and float(d_pnl) < -1e-12
    pf_up = d_pf is not None and d_pf != float("-inf") and float(d_pf) > 1e-12
    pf_down = d_pf is not None and (d_pf == float("-inf") or float(d_pf) < -1e-12)
    dd_ok = _finite(d_dd) and float(d_dd) >= -1e-12
    not_one_day = _finite(d_ex) and float(d_ex) > 1e-12 and _finite(d_top3) and float(d_top3) > 1e-12
    day_extreme = worsen_n > (improve_n * 2) and worsen_n >= 3
    freeze_next = (
        "Freeze this Branch U one-shot. Do not add Branch P here. "
        "Do not retune BB / hop to EMA / RCI / VWAP on these 18 days. TRUE_OOS=false. Not CERTIFIED."
    )

    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_BRANCH_U_EXIT_INVALID"
        next_step = "STOP. Identity, causality, control uniqueness, or non-interference failed."
    elif total_down or (not total_up and pf_down):
        case = "C"
        verdict = "SIMPLE_TECH_BRANCH_U_BB_EXIT_NOT_SUPPORTED"
        next_step = (
            "STOP. Actual U_BB_LOWER_BREAK EXIT did not improve portfolio economics. " + freeze_next
        )
    elif (
        total_up
        and pf_up
        and dd_ok
        and early_reduced
        and dip_ok
        and good_ok
        and not_one_day
        and (not day_extreme)
    ):
        case = "A"
        verdict = "SIMPLE_TECH_BRANCH_U_BB_EXIT_DEVELOPMENT_SUPPORTED"
        next_step = freeze_next
    else:
        case = "B"
        verdict = "SIMPLE_TECH_BRANCH_U_BB_EXIT_MIXED"
        next_step = (
            "STOP. EARLY vs DIP/GOOD tradeoff or incomplete economic gates remain. " + freeze_next
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V27_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": tested,
        "CONTROL_UNIQUE": control_ok,
        "BRANCH_U_EXIT_N": summary.get("BRANCH_U_EXIT_N"),
        "SESSION_CLOSE_EXIT_N": summary.get("SESSION_CLOSE_EXIT_N"),
        "DIP_BRANCH_U_EXIT_N": dip_u_n,
        "GOOD_BRANCH_U_EXIT_N": good_u_n,
        "EARLY_BRANCH_U_EXIT_N": summary.get("EARLY_BRANCH_U_EXIT_N"),
        "EARLY_DELTA_PNL": early_d,
        "DIP_DELTA_PNL": dip_d,
        "GOOD_DELTA_PNL": good_d,
        "PTF_DELTA_PNL": ptf.get("delta_pnl"),
        "OTHER_DELTA_PNL": other.get("delta_pnl"),
        "WINNER_HARM": winner_harm,
        "DELTA_TOTAL_PNL": d_pnl,
        "DELTA_PF": d_pf,
        "DELTA_MAX_DD": d_dd,
        "DELTA_EX_BEST_DAY": d_ex,
        "DELTA_EX_TOP3_DAY": d_top3,
        "DELTA_DROP_TOP_SYMBOL": summary.get("DELTA_DROP_TOP_SYMBOL"),
        "IMPROVE_DAY_N": improve_n,
        "WORSEN_DAY_N": worsen_n,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "BRANCH_P_ADDED": False,
    }

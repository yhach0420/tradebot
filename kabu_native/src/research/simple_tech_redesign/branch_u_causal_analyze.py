"""Causal portfolio economics vs occupancy Control. No alternate EXIT ranking. No fill forcing."""
from __future__ import annotations

from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_redesign.branch_u_bb_analyze import _public_econ
from research.simple_tech_redesign.branch_u_bb_spec import (
    B1_SIGNAL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    EXIT_REASON,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
)
from research.simple_tech_redesign.branch_u_causal_harvest import _sym
from research.simple_tech_redesign.branch_u_causal_spec import (
    DIP_PATH,
    EARLY_PATH,
    GOOD_PATH,
    ONE_SHOT_DIP_U_EXIT_N,
    ONE_SHOT_EARLY_U_EXIT_N,
    ONE_SHOT_GOOD_U_EXIT_N,
    PATH_TYPES,
)
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v28_analyze import _delta, economics
from research.simple_tech_strategy.v20_analyze import daily_yen


def _tid_map(trades: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out = {}
    for t in trades:
        t0 = t.get("t0")
        if not _finite(t0):
            continue
        out[(str(t.get("date") or ""), _sym(t), float(t0))] = t
    return out


def _as_arm_fills(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        out.append(
            {
                "date": t.get("date"),
                "symbol": _sym(t),
                "t0": t.get("t0"),
                "fill_t": t.get("fill_time"),
                "fill_price": t.get("fill_price"),
                "fill_role": t.get("fill_role"),
                "path_type": t.get("path_type"),
                "actual_filled": True,
                "control_exit": {
                    "exit_t": t.get("exit_time"),
                    "exit_bid": t.get("exit_price"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "reason": t.get("exit_reason"),
                    "miss": bool(t.get("exit_miss")),
                },
            }
        )
    return out


def econ(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return economics(_as_arm_fills(trades), "control_exit")


def _counts(arm: dict[str, Any]) -> dict[str, Any]:
    trades = list(arm.get("trades") or [])
    core = [t for t in trades if str(t.get("fill_role") or "") == "CORE"]
    added = [t for t in trades if str(t.get("fill_role") or "") == "ADDED"]
    return {
        "signal_n": arm.get("signal_n"),
        "candidate_n": arm.get("candidate_n"),
        "accepted_entry_n": arm.get("accepted_entry_n"),
        "fill_n": arm.get("fill_n"),
        "Branch_U_exit_n": arm.get("branch_u_exit_n"),
        "session_close_exit_n": arm.get("session_close_exit_n"),
        "slot_release_n": arm.get("slot_release_n"),
        "CAP_reject_n": arm.get("cap_blocked"),
        "same_symbol_reject_n": arm.get("same_symbol_blocked"),
        "core_fill_n": len(core),
        "added_fill_n": len(added),
        "open_leftover_n": arm.get("open_leftover_n"),
        "pending_leftover_n": arm.get("pending_leftover_n"),
        "expired_n": arm.get("expired_n"),
    }


def attribution(ctrl_trades: list[dict[str, Any]], treat_trades: list[dict[str, Any]]) -> dict[str, Any]:
    cm = _tid_map(ctrl_trades)
    tm = _tid_map(treat_trades)
    common = sorted(set(cm) & set(tm))
    treat_only = sorted(set(tm) - set(cm))
    ctrl_only = sorted(set(cm) - set(tm))
    direct = 0.0
    for k in common:
        direct += float(tm[k].get("pnl_yen_100") or 0.0) - float(cm[k].get("pnl_yen_100") or 0.0)
    downstream = float(sum(float(tm[k].get("pnl_yen_100") or 0.0) for k in treat_only))
    displaced = float(sum(0.0 - float(cm[k].get("pnl_yen_100") or 0.0) for k in ctrl_only))
    return {
        "COMMON_N": len(common),
        "INCREMENTAL_TREATMENT_N": len(treat_only),
        "CONTROL_ONLY_N": len(ctrl_only),
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": downstream,
        "DISPLACED_TRADE_DELTA": displaced,
        "TOTAL_CAUSAL_DELTA": direct + downstream + displaced,
        "common_ids": [f"{a}|{b}|{c}" for a, b, c in common],
        "incremental_ids": [f"{a}|{b}|{c}" for a, b, c in treat_only],
        "control_only_ids": [f"{a}|{b}|{c}" for a, b, c in ctrl_only],
    }


def oneshot_audit(
    rows: list[dict[str, Any]], treat_trades: list[dict[str, Any]], common_ids: set[tuple[str, str, float]]
) -> dict[str, Any]:
    u_ids = set()
    by_path = {p: 0 for p in PATH_TYPES}
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if str((r.get("treatment_exit") or {}).get("reason") or "") != EXIT_REASON:
            continue
        t0 = r.get("t0")
        if not _finite(t0):
            continue
        key = (str(r.get("date") or ""), _sym(r), float(t0))
        u_ids.add(key)
        p = str(r.get("path_type") or "")
        if p in by_path:
            by_path[p] += 1
    tm = _tid_map(treat_trades)
    causal_u = [t for t in treat_trades if str(t.get("exit_reason") or "") == EXIT_REASON]
    common_u_path = {p: 0 for p in PATH_TYPES}
    for t in causal_u:
        t0 = t.get("t0")
        if not _finite(t0):
            continue
        key = (str(t.get("date") or ""), _sym(t), float(t0))
        if key not in common_ids:
            continue
        p = str(t.get("path_type") or "")
        if p in common_u_path:
            common_u_path[p] += 1
    admitted_of_oneshot_u = sum(1 for k in u_ids if k in tm)
    return {
        "ONE_SHOT_U_EXIT_N": len(u_ids),
        "ONE_SHOT_EARLY_U_EXIT_N": by_path.get(EARLY_PATH, 0),
        "ONE_SHOT_DIP_U_EXIT_N": by_path.get(DIP_PATH, 0),
        "ONE_SHOT_GOOD_U_EXIT_N": by_path.get(GOOD_PATH, 0),
        "ONE_SHOT_U_ADMITTED_IN_TREATMENT_N": int(admitted_of_oneshot_u),
        "CAUSAL_U_EXIT_N": len(causal_u),
        "COMMON_EARLY_U_EXIT_N": common_u_path.get(EARLY_PATH, 0),
        "COMMON_DIP_U_EXIT_N": common_u_path.get(DIP_PATH, 0),
        "COMMON_GOOD_U_EXIT_N": common_u_path.get(GOOD_PATH, 0),
        "ONESHOT_EARLY_EXPECTED": int(ONE_SHOT_EARLY_U_EXIT_N),
        "ONESHOT_DIP_EXPECTED": int(ONE_SHOT_DIP_U_EXIT_N),
        "ONESHOT_GOOD_EXPECTED": int(ONE_SHOT_GOOD_U_EXIT_N),
        "ONESHOT_PATH_MATCH": (
            by_path.get(EARLY_PATH, 0) == int(ONE_SHOT_EARLY_U_EXIT_N)
            and by_path.get(DIP_PATH, 0) == int(ONE_SHOT_DIP_U_EXIT_N)
            and by_path.get(GOOD_PATH, 0) == int(ONE_SHOT_GOOD_U_EXIT_N)
        ),
    }


def day_table(ctrl_trades: list[dict[str, Any]], treat_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    c_daily = {str(r.get("date") or ""): r for r in daily_yen(ctrl_trades, list(ELIGIBLE_DAYS))}
    t_daily = {str(r.get("date") or ""): r for r in daily_yen(treat_trades, list(ELIGIBLE_DAYS))}
    treat_by_day: dict[str, list[dict[str, Any]]] = {}
    ctrl_ids = set(_tid_map(ctrl_trades))
    for t in treat_trades:
        treat_by_day.setdefault(str(t.get("date") or ""), []).append(t)
    rows = []
    for day in list(ELIGIBLE_DAYS):
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
                "side": side,
            }
        )
    return rows


def summarize(
    rows: list[dict[str, Any]],
    *,
    ctrl: dict[str, Any],
    treat: dict[str, Any],
    parity: dict[str, Any],
) -> dict[str, Any]:
    ctrl_tr = list(ctrl.get("trades") or [])
    treat_tr = list(treat.get("trades") or [])
    attr = attribution(ctrl_tr, treat_tr)
    common_ids: set[tuple[str, str, float]] = set()
    for s in list(attr.get("common_ids") or []):
        parts = str(s).split("|")
        if len(parts) == 3:
            try:
                common_ids.add((parts[0], parts[1], float(parts[2])))
            except ValueError:
                continue
    oneshot = oneshot_audit(rows, treat_tr, common_ids)
    c_econ = econ(ctrl_tr)
    t_econ = econ(treat_tr)
    core_c = econ([x for x in ctrl_tr if str(x.get("fill_role") or "") == "CORE"])
    core_t = econ([x for x in treat_tr if str(x.get("fill_role") or "") == "CORE"])
    add_c = econ([x for x in ctrl_tr if str(x.get("fill_role") or "") == "ADDED"])
    add_t = econ([x for x in treat_tr if str(x.get("fill_role") or "") == "ADDED"])
    cm = _tid_map(ctrl_tr)
    tm = _tid_map(treat_tr)
    common_c = [cm[k] for k in sorted(set(cm) & set(tm))]
    common_t = [tm[k] for k in sorted(set(cm) & set(tm))]
    incr = [tm[k] for k in sorted(set(tm) - set(cm))]
    days = day_table(ctrl_tr, treat_tr)
    improve = sum(1 for r in days if r["side"] == "IMPROVE")
    worsen = sum(1 for r in days if r["side"] == "WORSEN")
    flat = sum(1 for r in days if r["side"] == "FLAT")
    deltas = {
        "DELTA_TOTAL_PNL": _delta(t_econ, c_econ, "TOTAL_PNL_YEN"),
        "DELTA_PF": _delta(t_econ, c_econ, "PF"),
        "DELTA_MAX_DD": _delta(t_econ, c_econ, "REALIZED_MAX_DD"),
        "DELTA_EX_BEST_DAY": _delta(t_econ, c_econ, "EX_BEST_DAY_TOTAL_PNL"),
        "DELTA_EX_TOP3_DAY": _delta(t_econ, c_econ, "EX_TOP3_DAY_TOTAL_PNL"),
        "DELTA_DROP_TOP_SYMBOL": _delta(t_econ, c_econ, "DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DELTA_LODO_MIN": _delta(t_econ, c_econ, "LODO_MIN_TOTAL_PNL"),
        "DELTA_LODO_MEDIAN": _delta(t_econ, c_econ, "LODO_MEDIAN_TOTAL_PNL"),
    }
    fills_unconst = [r for r in rows if r.get("actual_filled")]
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "UNCONSTRAINED_CORE_FILL_N": sum(1 for r in fills_unconst if str(r.get("fill_role") or "") == "CORE"),
        "UNCONSTRAINED_ADDED_FILL_N": sum(1 for r in fills_unconst if str(r.get("fill_role") or "") == "ADDED"),
        "UNCONSTRAINED_FILL_N": len(fills_unconst),
        "CORRECTED_FILL_HASH": set_hash(fill_tuples_e4(rows)),
        "RESEARCH_FILL_SET_HASH": set_hash(research_fill_tuples(rows)),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
        "CONTROL_COUNTS": _counts(ctrl),
        "TREATMENT_COUNTS": _counts(treat),
        "incremental_treatment_trade_n": attr.get("INCREMENTAL_TREATMENT_N"),
        "control_only_trade_n": attr.get("CONTROL_ONLY_N"),
        "CONTROL_ECONOMICS": _public_econ(c_econ),
        "TREATMENT_ECONOMICS": _public_econ(t_econ),
        "CORE_CONTROL_ECONOMICS": _public_econ(core_c),
        "CORE_TREATMENT_ECONOMICS": _public_econ(core_t),
        "ADDED_CONTROL_ECONOMICS": _public_econ(add_c),
        "ADDED_TREATMENT_ECONOMICS": _public_econ(add_t),
        "COMMON_CONTROL_ECONOMICS": _public_econ(econ(common_c)),
        "COMMON_TREATMENT_ECONOMICS": _public_econ(econ(common_t)),
        "INCREMENTAL_ECONOMICS": _public_econ(econ(incr)),
        "ATTRIBUTION": {
            "DIRECT_EXIT_DELTA": attr.get("DIRECT_EXIT_DELTA"),
            "SLOT_RELEASE_DOWNSTREAM_DELTA": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "DISPLACED_TRADE_DELTA": attr.get("DISPLACED_TRADE_DELTA"),
            "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
            "COMMON_N": attr.get("COMMON_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
        },
        "ONE_SHOT_AUDIT": oneshot,
        "DAY_ROBUSTNESS": {
            "IMPROVE_DAY_N": int(improve),
            "WORSEN_DAY_N": int(worsen),
            "FLAT_DAY_N": int(flat),
            "daily": days,
        },
        "PARITY": parity,
        **deltas,
        "trades_control": ctrl_tr,
        "trades_treatment": treat_tr,
        "incremental_ids": attr.get("incremental_ids"),
        "control_only_ids": attr.get("control_only_ids"),
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    fill_identity: bool,
    leak_ok: bool,
    ni_ok: bool,
    occupancy_ok: bool,
    pre_div_ok: bool,
    leftover_ok: bool,
) -> dict[str, Any]:
    tested = list(summary.get("TESTED_BRANCH_U_EXIT_MECHANISMS") or [])
    tested_ok = tested == ["U_BB_LOWER_BREAK"]
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("UNCONSTRAINED_FILL_N") or 0) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
        and int(summary.get("UNCONSTRAINED_CORE_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
    )
    hash_ok = (
        str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
        and str(summary.get("RESEARCH_FILL_SET_HASH") or "") == RESEARCH_FILL_SET_HASH_EXPECTED
        and bool(fill_identity)
    )
    oneshot = dict(summary.get("ONE_SHOT_AUDIT") or {})
    oneshot_ok = bool(oneshot.get("ONESHOT_PATH_MATCH"))
    integrity = bool(
        leak_ok
        and ni_ok
        and signal_parity
        and counts_ok
        and hash_ok
        and tested_ok
        and occupancy_ok
        and pre_div_ok
        and leftover_ok
        and oneshot_ok
    )
    attr = dict(summary.get("ATTRIBUTION") or {})
    d_pnl = summary.get("DELTA_TOTAL_PNL")
    d_pf = summary.get("DELTA_PF")
    d_dd = summary.get("DELTA_MAX_DD")
    d_ex = summary.get("DELTA_EX_BEST_DAY")
    d_top3 = summary.get("DELTA_EX_TOP3_DAY")
    direct = attr.get("DIRECT_EXIT_DELTA")
    down = attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA")
    day = dict(summary.get("DAY_ROBUSTNESS") or {})
    improve_n = int(day.get("IMPROVE_DAY_N") or 0)
    worsen_n = int(day.get("WORSEN_DAY_N") or 0)
    total_up = _finite(d_pnl) and float(d_pnl) > 1e-12
    total_down = _finite(d_pnl) and float(d_pnl) < -1e-12
    pf_up = d_pf is not None and d_pf != float("-inf") and float(d_pf) > 1e-12
    dd_ok = _finite(d_dd) and float(d_dd) >= -1e-12
    direct_ok = _finite(direct) and float(direct) > 1e-12
    down_f = float(down or 0.0)
    destructive = _finite(direct) and float(direct) > 1e-12 and down_f < -0.5 * float(direct)
    not_one_day = _finite(d_ex) and float(d_ex) > 1e-12 and _finite(d_top3) and float(d_top3) > 1e-12
    day_extreme = worsen_n > (improve_n * 2) and worsen_n >= 3
    freeze = (
        "Freeze this causal portfolio result. Do not add Branch P. "
        "Do not retune BB / hop to EMA / RCI / VWAP on these 18 days. TRUE_OOS=false. CERTIFIED=false."
    )
    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_BRANCH_U_FULL_CAUSAL_REPLAY_INVALID"
        next_step = "STOP. Pre-divergence, occupancy SoT, identity, or non-interference failed."
    elif total_down or (not total_up and not direct_ok):
        case = "C"
        verdict = "SIMPLE_TECH_BRANCH_U_CAUSAL_PORTFOLIO_NOT_SUPPORTED"
        next_step = "STOP. Slot-release feedback does not keep a portfolio improvement. Do not CERTIFY Branch U. " + freeze
    elif (
        total_up
        and pf_up
        and dd_ok
        and direct_ok
        and (not destructive)
        and not_one_day
        and (not day_extreme)
    ):
        case = "A"
        verdict = "SIMPLE_TECH_BRANCH_U_CAUSAL_PORTFOLIO_SUPPORTED"
        next_step = freeze
    else:
        case = "B"
        verdict = "SIMPLE_TECH_BRANCH_U_CAUSAL_PORTFOLIO_MIXED"
        next_step = "STOP. Direct EXIT benefit and downstream occupancy effects remain mixed. " + freeze
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V27_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "TESTED_BRANCH_U_EXIT_MECHANISMS": tested,
        "FORCE_TREATMENT_FILL_SET": False,
        "OCCUPANCY_SOT_PARITY": occupancy_ok,
        "PRE_DIVERGENCE_PARITY": pre_div_ok,
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": down,
        "DISPLACED_TRADE_DELTA": attr.get("DISPLACED_TRADE_DELTA"),
        "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
        "DELTA_TOTAL_PNL": d_pnl,
        "DELTA_PF": d_pf,
        "DELTA_MAX_DD": d_dd,
        "DELTA_EX_BEST_DAY": d_ex,
        "DELTA_EX_TOP3_DAY": d_top3,
        "IMPROVE_DAY_N": improve_n,
        "WORSEN_DAY_N": worsen_n,
        "COMMON_DIP_U_EXIT_N": oneshot.get("COMMON_DIP_U_EXIT_N"),
        "COMMON_GOOD_U_EXIT_N": oneshot.get("COMMON_GOOD_U_EXIT_N"),
        "COMMON_EARLY_U_EXIT_N": oneshot.get("COMMON_EARLY_U_EXIT_N"),
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "BRANCH_P_ADDED": False,
        "V1R_PROCESS_MARKET_PUSH_USED": False,
    }

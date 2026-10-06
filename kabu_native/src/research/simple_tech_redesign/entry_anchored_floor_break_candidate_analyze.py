"""Full causal portfolio economics for ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Optional

from replay.pnl_yen import summarize_pnl_yen_100
from research.anchor_timing_robustness.metrics import maxdd
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_redesign.branch_u_causal_analyze import _tid_map, attribution
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    CANDIDATE_EXIT_REASON,
    DECOMP_TOL,
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_DISCOVERY_FLOOR_FIRST,
    DEV_FILL_N,
    DEV_PNL,
    FAILURE_CLASSES,
    FIRST_PROSPECTIVE_DAY,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    PROTECTED_CLASSES,
    YEN_PARITY_TOL,
)
from research.simple_tech_strategy.v20_analyze import daily_yen

EPS = 1e-9
CLASS_SHORT = {
    "U_EARLY_NEVER_BE": "U",
    "P_EARLY_AFTER_BE": "P_EARLY",
    "P_PROFIT_THEN_FAILURE": "PTF",
    "PROTECTED_DIP": "DIP",
    "PROTECTED_GOOD": "GOOD",
}
SHORT_TO_CLASS = {v: k for k, v in CLASS_SHORT.items()}
DIAG_ORDER = ("U", "P_EARLY", "PTF", "DIP", "GOOD", "OTHER")


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


def _tid(row: dict[str, Any]) -> Optional[tuple[str, str, float]]:
    t0 = _f(row.get("t0") or row.get("signal_time"))
    if t0 is None:
        return None
    return str(row.get("date") or ""), _bare(row.get("symbol")), float(t0)


def _tid_s(key: tuple[str, str, float]) -> str:
    return f"{key[0]}|{key[1]}|{key[2]}"


def residual_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    out = {}
    for r in rows:
        key = _tid(r)
        if key is None:
            continue
        out[key] = r
    return out


def row_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, float], dict[str, Any]]:
    return residual_index(rows)


def class_of(key: tuple[str, str, float], residual: dict[tuple[str, str, float], dict[str, Any]]) -> str:
    cls = str((residual.get(key) or {}).get("residual_class") or "")
    if cls in CLASS_SHORT:
        return cls
    return "OTHER"


def short_class(cls: str) -> str:
    return CLASS_SHORT.get(cls, "OTHER")


def identity_check(
    trades: list[dict[str, Any]],
    *,
    fill_n: int,
    core_n: int,
    added_n: int,
    pnl: float,
) -> dict[str, Any]:
    got_n = len(trades)
    got_core = sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE")
    got_added = sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED")
    got_pnl = float(sum(float(_f(t.get("pnl_yen_100")) or 0.0) for t in trades))
    ok = (
        got_n == int(fill_n)
        and got_core == int(core_n)
        and got_added == int(added_n)
        and abs(got_pnl - float(pnl)) <= YEN_PARITY_TOL
    )
    return {
        "ok": bool(ok),
        "fill_n": got_n,
        "core_n": got_core,
        "added_n": got_added,
        "pnl": got_pnl,
        "expected_fill_n": int(fill_n),
        "expected_core_n": int(core_n),
        "expected_added_n": int(added_n),
        "expected_pnl": float(pnl),
    }


def _pf_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and str(v).lower() == "inf":
        return float("inf")
    if _f(v) is not None:
        return float(v)
    return None


def arm_econ(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "fill_n": 0,
            "total_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "PF": None,
            "win_rate": None,
            "max_drawdown": 0.0,
            "win_n": 0,
            "loss_n": 0,
        }
    summ = summarize_pnl_yen_100(trades)
    dd = float(maxdd(trades, time_key="exit_time", pnl_key="pnl_yen_100"))
    n = len(trades)
    wins = sum(1 for t in trades if float(_f(t.get("pnl_yen_100")) or 0.0) > EPS)
    return {
        "fill_n": n,
        "total_pnl": float(summ.get("total_pnl_yen_100") or 0.0),
        "gross_profit": float(summ.get("gross_profit_yen_100") or 0.0),
        "gross_loss": float(summ.get("gross_loss_yen_100") or 0.0),
        "PF": summ.get("profit_factor_yen_100"),
        "win_rate": wins / n if n else None,
        "max_drawdown": dd,
        "win_n": wins,
        "loss_n": sum(1 for t in trades if float(_f(t.get("pnl_yen_100")) or 0.0) < -EPS),
    }


def arm_counts(arm: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    tech = sum(1 for t in trades if str(t.get("exit_reason") or "") == CANDIDATE_EXIT_REASON)
    close_n = sum(1 for t in trades if str(t.get("exit_reason") or "") != CANDIDATE_EXIT_REASON)
    return {
        "fill_n": int(arm.get("fill_n") or len(trades)),
        "exit_n": int(arm.get("slot_release_n") or len(trades)),
        "session_close_n": int(close_n),
        "candidate_exit_n": int(tech),
        "slot_release_n": int(arm.get("slot_release_n") or 0),
        "CAP_reject_n": int(arm.get("cap_blocked") or 0),
        "same_symbol_reject_n": int(arm.get("same_symbol_blocked") or 0),
        "core_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "CORE"),
        "added_fill_n": sum(1 for t in trades if str(t.get("fill_role") or "") == "ADDED"),
        "open_leftover_n": int(arm.get("open_leftover_n") or 0),
        "pending_leftover_n": int(arm.get("pending_leftover_n") or 0),
    }


def portfolio_pack(arm: dict[str, Any]) -> dict[str, Any]:
    trades = list(arm.get("trades") or [])
    econ = arm_econ(trades)
    counts = arm_counts(arm, trades)
    return {**counts, **econ}


def _path(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row.get("candidate_path") or {})


def delta_pack(deltas: list[float]) -> dict[str, Any]:
    pos = sum(1 for d in deltas if d > EPS)
    neg = sum(1 for d in deltas if d < -EPS)
    return {
        "trigger_n": len(deltas),
        "direct_delta": float(sum(deltas)),
        "median_delta": float(median(deltas)) if deltas else None,
        "positive_delta_n": pos,
        "negative_delta_n": neg,
    }


def winner_class_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trig = []
    for r in rows:
        trig.append(
            {
                "trade_id": r.get("trade_id"),
                "candidate_exit_pnl": r.get("treat_pnl"),
                "control_close_pnl": r.get("ctrl_pnl"),
                "delta": r.get("delta"),
            }
        )
    return {
        "trigger_n": len(rows),
        "candidate_exit_pnl": float(sum(float(r.get("treat_pnl") or 0.0) for r in rows)),
        "control_close_pnl": float(sum(float(r.get("ctrl_pnl") or 0.0) for r in rows)),
        "delta": float(sum(float(r.get("delta") or 0.0) for r in rows)),
        "trades": trig,
    }


def day_table(ctrl_trades: list[dict[str, Any]], treat_trades: list[dict[str, Any]], days: list[str]) -> list[dict[str, Any]]:
    c_daily = {str(r.get("date") or ""): r for r in daily_yen(ctrl_trades, list(days))}
    t_daily = {str(r.get("date") or ""): r for r in daily_yen(treat_trades, list(days))}
    out = []
    for day in days:
        c_pnl = float((c_daily.get(day) or {}).get("net_pnl") or 0.0)
        t_pnl = float((t_daily.get(day) or {}).get("net_pnl") or 0.0)
        delta = t_pnl - c_pnl
        if delta > EPS:
            side = "IMPROVE"
        elif delta < -EPS:
            side = "WORSEN"
        else:
            side = "FLAT"
        out.append(
            {
                "date": day,
                "control_pnl": c_pnl,
                "treatment_pnl": t_pnl,
                "delta": delta,
                "side": side,
                "control_fill_n": int((c_daily.get(day) or {}).get("trade_n") or 0),
                "treatment_fill_n": int((t_daily.get(day) or {}).get("trade_n") or 0),
            }
        )
    return out


def concentration_pack(days: list[dict[str, Any]], ctrl_trades: list[dict[str, Any]], treat_trades: list[dict[str, Any]], total: float) -> dict[str, Any]:
    pos_days = [d for d in days if float(d.get("delta") or 0.0) > EPS]
    top_day = max(pos_days, key=lambda r: float(r.get("delta") or 0.0), default=None)
    loo_day_total = None
    loo_day_share = None
    if top_day is not None:
        loo_day_total = float(total) - float(top_day.get("delta") or 0.0)
        loo_day_share = (float(top_day.get("delta") or 0.0) / float(total)) if abs(total) > EPS else None
    cm = _tid_map(ctrl_trades)
    tm = _tid_map(treat_trades)
    sym_delta: dict[str, float] = defaultdict(float)
    for k, t in tm.items():
        c = cm.get(k)
        c_pnl = float(c.get("pnl_yen_100") or 0.0) if c else 0.0
        sym_delta[k[1]] += float(t.get("pnl_yen_100") or 0.0) - c_pnl
    for k, c in cm.items():
        if k not in tm:
            sym_delta[k[1]] += 0.0 - float(c.get("pnl_yen_100") or 0.0)
    pos_syms = [(s, v) for s, v in sym_delta.items() if v > EPS]
    top_sym = max(pos_syms, key=lambda x: x[1], default=None)
    loo_sym_total = None
    loo_sym_share = None
    if top_sym is not None:
        loo_sym_total = float(total) - float(top_sym[1])
        loo_sym_share = (float(top_sym[1]) / float(total)) if abs(total) > EPS else None
    warn = False
    if loo_day_share is not None and loo_day_share > 0.5:
        warn = True
    if loo_sym_share is not None and loo_sym_share > 0.5:
        warn = True
    collapse = False
    if total > EPS:
        if loo_day_total is not None and loo_day_total <= EPS:
            collapse = True
        if loo_sym_total is not None and loo_sym_total <= EPS:
            collapse = True
    return {
        "full_total_causal_delta": float(total),
        "largest_positive_delta_day": None if top_day is None else top_day.get("date"),
        "largest_positive_delta_day_value": None if top_day is None else top_day.get("delta"),
        "leave_one_largest_positive_delta_day": loo_day_total,
        "largest_positive_day_share": loo_day_share,
        "largest_positive_delta_symbol": None if top_sym is None else top_sym[0],
        "largest_positive_delta_symbol_value": None if top_sym is None else top_sym[1],
        "leave_one_largest_positive_delta_symbol": loo_sym_total,
        "largest_positive_symbol_share": loo_sym_share,
        "concentration_warning_gt_50pct": bool(warn),
        "support_collapse": bool(collapse),
    }


def boundary_audit(
    ctrl_trades: list[dict[str, Any]],
    rows_by: dict[tuple[str, str, float], dict[str, Any]],
) -> dict[str, Any]:
    a: list[dict[str, Any]] = []
    b: list[dict[str, Any]] = []
    c: list[dict[str, Any]] = []
    for t in ctrl_trades:
        key = _tid(t)
        if key is None:
            continue
        cp = _path(rows_by.get(key) or {})
        rec = {
            "trade_id": _tid_s(key),
            "date": key[0],
            "symbol": key[1],
            "t0": key[2],
            "rca_sequence": cp.get("rca_sequence"),
            "postfill_sequence": cp.get("postfill_sequence"),
            "prefill_upside": bool(cp.get("prefill_upside")),
            "prefill_floor": bool(cp.get("prefill_floor")),
            "triggered": bool(cp.get("triggered")),
            "locked": bool(cp.get("locked")),
        }
        if rec["prefill_upside"]:
            a.append(rec)
        if rec["prefill_floor"]:
            b.append(rec)
        if str(cp.get("rca_sequence") or "") != str(cp.get("postfill_sequence") or ""):
            c.append(rec)
    return {
        "A_prefill_upside_n": len(a),
        "B_prefill_floor_n": len(b),
        "C_postfill_vs_rca_n": len(c),
        "A_trades": a,
        "B_trades": b,
        "C_trades": c,
        "runtime_semantics": {
            "prefill_upside": "fill starts UPSIDE_BREAK_LOCKED",
            "prefill_floor": "ENTRY unchanged; EXIT at first causal Bid after fill",
            "entry_gate_changed": False,
        },
    }


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
    rows_by = row_index(harvest_rows)
    if cohort == "DEVELOPMENT":
        ident = identity_check(ctrl_tr, fill_n=DEV_FILL_N, core_n=DEV_CORE_N, added_n=DEV_ADDED_N, pnl=DEV_PNL)
    else:
        ident = identity_check(ctrl_tr, fill_n=FWD_FILL_N, core_n=FWD_CORE_N, added_n=FWD_ADDED_N, pnl=FWD_PNL)
    ident["control_sot_ok"] = bool(control_sot_ok)
    ident["treatment_sot_ok"] = bool(treatment_sot_ok)
    ident["leftover_ok"] = bool(leftover_ok)
    ref_fail = []
    for t in ctrl_tr + treat_tr:
        key = _tid(t)
        if key is None:
            continue
        cp = _path(rows_by.get(key) or {})
        if cp and not bool(cp.get("reference_ok")):
            ref_fail.append({"trade_id": _tid_s(key), "blocker": cp.get("reference_blocker")})
    ident["reference_fail_n"] = len(ref_fail)
    ident["reference_fail_sample"] = ref_fail[:20]
    attr = attribution(ctrl_tr, treat_tr)
    direct = float(attr.get("DIRECT_EXIT_DELTA") or 0.0)
    downstream = float(attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)
    displaced = float(attr.get("DISPLACED_TRADE_DELTA") or 0.0)
    slot = downstream + displaced
    total = float(attr.get("TOTAL_CAUSAL_DELTA") or 0.0)
    decomp_ok = abs((direct + slot) - total) <= DECOMP_TOL
    ctrl_port = portfolio_pack(ctrl_arm)
    treat_port = portfolio_pack(treat_arm)
    cm = _tid_map(ctrl_tr)
    tm = _tid_map(treat_tr)
    common = sorted(set(cm) & set(tm))
    treat_only = sorted(set(tm) - set(cm))
    cause_by = {}
    for c in causes:
        key = _tid(c)
        if key is not None:
            cause_by[key] = c
    shared_trigger: list[dict[str, Any]] = []
    by_short: dict[str, list[float]] = {k: [] for k in DIAG_ORDER}
    by_short_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in DIAG_ORDER}
    discovery_mismatch: list[dict[str, Any]] = []
    class_trigger: dict[str, int] = defaultdict(int)
    class_n: dict[str, int] = defaultdict(int)
    for t in ctrl_tr:
        key = _tid(t)
        if key is None:
            continue
        cls = class_of(key, residual)
        class_n[cls] += 1
        cp = _path(rows_by.get(key) or {})
        if bool(cp.get("triggered")):
            class_trigger[cls] += 1
    if cohort == "DEVELOPMENT":
        for cls, exp in DEV_DISCOVERY_FLOOR_FIRST.items():
            got = int(class_trigger.get(cls, 0))
            exp_n = int(exp.get("floor_first_n") or 0)
            if got != exp_n:
                discovery_mismatch.append(
                    {
                        "residual_class": cls,
                        "discovery_floor_first_n": exp_n,
                        "discovery_class_n": exp.get("class_n"),
                        "candidate_trigger_n": got,
                        "occupancy_class_n": int(class_n.get(cls, 0)),
                    }
                )
        for t in ctrl_tr:
            key = _tid(t)
            if key is None:
                continue
            cls = class_of(key, residual)
            cp = _path(rows_by.get(key) or {})
            rca_floor_first = str(cp.get("rca_sequence") or "") == "B_FLOOR_BREAK_FIRST"
            trig = bool(cp.get("triggered"))
            if rca_floor_first != trig:
                discovery_mismatch.append(
                    {
                        "trade_id": _tid_s(key),
                        "residual_class": cls,
                        "rca_sequence": cp.get("rca_sequence"),
                        "postfill_sequence": cp.get("postfill_sequence"),
                        "triggered": trig,
                        "would_trigger": cp.get("would_trigger"),
                        "exit_miss_fallback_session_close": cp.get("exit_miss_fallback_session_close"),
                        "prefill_upside": cp.get("prefill_upside"),
                        "prefill_floor": cp.get("prefill_floor"),
                        "locked": cp.get("locked"),
                    }
                )
    for key in common:
        c = cm[key]
        t = tm[key]
        cp = _path(rows_by.get(key) or {})
        if not bool(cp.get("triggered")) and str(t.get("exit_reason") or "") != CANDIDATE_EXIT_REASON:
            continue
        cls = class_of(key, residual)
        sh = short_class(cls)
        dlt = float(t.get("pnl_yen_100") or 0.0) - float(c.get("pnl_yen_100") or 0.0)
        rec = {
            "trade_id": _tid_s(key),
            "date": key[0],
            "symbol": key[1],
            "t0": key[2],
            "fill_role": c.get("fill_role"),
            "residual_class": cls,
            "short_class": sh,
            "ctrl_pnl": float(c.get("pnl_yen_100") or 0.0),
            "treat_pnl": float(t.get("pnl_yen_100") or 0.0),
            "delta": dlt,
            "rca_sequence": cp.get("rca_sequence"),
            "postfill_sequence": cp.get("postfill_sequence"),
            "prefill_floor": cp.get("prefill_floor"),
            "prefill_upside": cp.get("prefill_upside"),
            "locked": cp.get("locked"),
        }
        shared_trigger.append(rec)
        by_short.setdefault(sh, []).append(dlt)
        by_short_rows.setdefault(sh, []).append(rec)
    class_diag = {k: delta_pack(by_short.get(k) or []) for k in DIAG_ORDER}
    fail_delta = float(sum((by_short.get("U") or []) + (by_short.get("P_EARLY") or []) + (by_short.get("PTF") or [])))
    harm_delta = float(sum((by_short.get("DIP") or []) + (by_short.get("GOOD") or [])))
    incr_trades = [tm[k] for k in treat_only]
    incr_econ = arm_econ(incr_trades)
    incr_rows = []
    for k in treat_only:
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
    days_tbl = day_table(ctrl_tr, treat_tr, days)
    improve_n = sum(1 for r in days_tbl if r["side"] == "IMPROVE")
    worsen_n = sum(1 for r in days_tbl if r["side"] == "WORSEN")
    flat_n = sum(1 for r in days_tbl if r["side"] == "FLAT")
    pos_days = sorted([r for r in days_tbl if float(r.get("delta") or 0.0) > EPS], key=lambda r: float(r["delta"]), reverse=True)
    neg_days = sorted([r for r in days_tbl if float(r.get("delta") or 0.0) < -EPS], key=lambda r: float(r["delta"]))
    top_pos = pos_days[0] if pos_days else None
    top_neg = neg_days[0] if neg_days else None
    pos_sum = float(sum(float(r["delta"]) for r in pos_days))
    top_pos_share = (float(top_pos["delta"]) / pos_sum) if top_pos is not None and pos_sum > EPS else None
    conc = concentration_pack(days_tbl, ctrl_tr, treat_tr, total)
    core_trig = [r for r in shared_trigger if str(r.get("fill_role") or "") == "CORE"]
    added_trig = [r for r in shared_trigger if str(r.get("fill_role") or "") == "ADDED"]
    core_direct = float(sum(float(r.get("delta") or 0.0) for r in core_trig))
    added_direct = float(sum(float(r.get("delta") or 0.0) for r in added_trig))
    role_port = {
        "CORE": {
            "control": arm_econ([t for t in ctrl_tr if str(t.get("fill_role") or "") == "CORE"]),
            "treatment": arm_econ([t for t in treat_tr if str(t.get("fill_role") or "") == "CORE"]),
            "direct_trigger_n": len(core_trig),
            "direct_delta": core_direct,
        },
        "ADDED": {
            "control": arm_econ([t for t in ctrl_tr if str(t.get("fill_role") or "") == "ADDED"]),
            "treatment": arm_econ([t for t in treat_tr if str(t.get("fill_role") or "") == "ADDED"]),
            "direct_trigger_n": len(added_trig),
            "direct_delta": added_direct,
        },
    }
    boundary = boundary_audit(ctrl_tr, rows_by)
    return {
        "cohort": cohort,
        "identity": ident,
        "boundary": boundary,
        "trigger_n": len(shared_trigger),
        "occupancy_control_trigger_n": int(sum(class_trigger.values())),
        "class_occupancy_n": dict(class_n),
        "class_trigger_n": dict(class_trigger),
        "discovery_mismatch": discovery_mismatch,
        "DIRECT_EXIT_DELTA": direct,
        "SLOT_RELEASE_DOWNSTREAM_DELTA": slot,
        "SLOT_RELEASE_INCREMENTAL_PNL": downstream,
        "DISPLACED_TRADE_DELTA": displaced,
        "TOTAL_CAUSAL_DELTA": total,
        "decomp_ok": bool(decomp_ok),
        "winner_harm_delta": harm_delta,
        "failure_saved_delta": fail_delta,
        "class_diag": class_diag,
        "winner_harm": {
            "DIP": winner_class_pack(by_short_rows.get("DIP") or []),
            "GOOD": winner_class_pack(by_short_rows.get("GOOD") or []),
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
        "day_summary": {
            "improve_day_n": improve_n,
            "worsen_day_n": worsen_n,
            "flat_day_n": flat_n,
            "top_positive_day": None if top_pos is None else top_pos.get("date"),
            "top_positive_day_delta": None if top_pos is None else top_pos.get("delta"),
            "top_positive_day_share_of_positive": top_pos_share,
            "top_negative_day": None if top_neg is None else top_neg.get("date"),
            "top_negative_day_delta": None if top_neg is None else top_neg.get("delta"),
        },
        "concentration": conc,
        "core_added": role_port,
        "shared_trigger_trades": shared_trigger,
        "attribution_raw": {
            "COMMON_N": attr.get("COMMON_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
        },
        "integrity_extra": {
            "reference_fail_n": len(ref_fail),
            "pre_fill_exit_blocked": True,
        },
    }


def _pf_gt(treat: Any, ctrl: Any) -> bool:
    ta, tb = _pf_num(treat), _pf_num(ctrl)
    if ta is None or tb is None:
        return False
    return float(ta) > float(tb) + EPS


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    sot_ok = bool(dev.get("identity", {}).get("control_sot_ok")) and bool(fwd.get("identity", {}).get("control_sot_ok"))
    leftover_ok = bool(dev.get("identity", {}).get("leftover_ok")) and bool(fwd.get("identity", {}).get("leftover_ok"))
    decomp_ok = bool(dev.get("decomp_ok")) and bool(fwd.get("decomp_ok"))
    ref_ok = int(dev.get("identity", {}).get("reference_fail_n") or 0) == 0 and int(fwd.get("identity", {}).get("reference_fail_n") or 0) == 0
    integrity = bool(leak_ok and harvested_ok and ident_ok and sot_ok and leftover_ok and decomp_ok and ref_ok)
    direct = float(dev.get("DIRECT_EXIT_DELTA") or 0.0)
    total = float(dev.get("TOTAL_CAUSAL_DELTA") or 0.0)
    slot = float(dev.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)
    harm = float(dev.get("winner_harm_delta") or 0.0)
    saved = float(dev.get("failure_saved_delta") or 0.0)
    added_direct = float(((dev.get("core_added") or {}).get("ADDED") or {}).get("direct_delta") or 0.0)
    c_pf = (dev.get("control") or {}).get("PF")
    t_pf = (dev.get("treatment") or {}).get("PF")
    c_dd = float((dev.get("control") or {}).get("max_drawdown") or 0.0)
    t_dd = float((dev.get("treatment") or {}).get("max_drawdown") or 0.0)
    pf_up = _pf_gt(t_pf, c_pf)
    dd_not_worse = t_dd + EPS >= c_dd
    conc = dict(dev.get("concentration") or {})
    collapse = bool(conc.get("support_collapse"))
    conc_warn = bool(conc.get("concentration_warning_gt_50pct"))
    fwd_direct = float(fwd.get("DIRECT_EXIT_DELTA") or 0.0)
    fwd_total = float(fwd.get("TOTAL_CAUSAL_DELTA") or 0.0)
    fwd_reverse = (total > EPS and fwd_total < -EPS) and (direct > EPS and fwd_direct < -EPS)
    winner_dominates = harm < -EPS and saved > EPS and abs(harm) + EPS >= saved
    frozen = False
    prospective = None
    if not integrity:
        case = "F"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INTEGRITY_FAILED"
        nxt = "FAIL_CLOSED. Control identity, causal ordering, reference, or executability mismatch."
    elif winner_dominates or (direct <= EPS and harm < -EPS and saved > EPS):
        case = "C"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_WINNER_HARM"
        nxt = "candidate reject. family CLOSE. STOP."
    elif direct > EPS and total <= EPS:
        case = "B"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_DIRECT_GOOD_PORTFOLIO_BAD"
        nxt = "candidate reject. family CLOSE. STOP. Slot-release downstream damage."
    elif total <= EPS or direct <= EPS:
        case = "E"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_NO_ECONOMIC_GAIN"
        nxt = "candidate reject. family CLOSE. STOP."
    elif (
        total > EPS
        and direct > EPS
        and pf_up
        and dd_not_worse
        and (not winner_dominates)
        and added_direct > EPS
        and (not collapse)
        and (not fwd_reverse)
    ):
        case = "A"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_SUPPORTED"
        nxt = (
            f"Freeze ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1. No new parameters. "
            f"First eligible prospective full-day: {FIRST_PROSPECTIVE_DAY}. Do not open 20260903/20260904."
        )
        frozen = True
        prospective = FIRST_PROSPECTIVE_DAY
    else:
        case = "D"
        verdict = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_ROBUSTNESS_FAILED"
        reasons = []
        if not pf_up:
            reasons.append("Treatment PF not greater than Control PF")
        if not dd_not_worse:
            reasons.append("Treatment max DD worse than Control")
        if added_direct <= EPS:
            reasons.append("ADDED direct delta not positive")
        if collapse:
            reasons.append("concentration LOO collapsed support")
        if fwd_reverse:
            reasons.append("FWD_BURNED total and direct reversed vs DEV")
        if conc_warn and not collapse:
            reasons.append("concentration warning >50% without full collapse")
        nxt = "candidate freeze forbidden. family CLOSE. STOP. " + "; ".join(reasons)
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "CANDIDATE_FROZEN": bool(frozen),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "PRIMARY_NEXT_EXIT_TARGET": "ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1" if frozen else None,
        "first_eligible_prospective_date": prospective,
        "NEW_EXIT_RULE": False,
        "THRESHOLD_SEARCH": False,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "gates": {
            "integrity": integrity,
            "DEV_TOTAL_CAUSAL_DELTA_gt_0": total > EPS,
            "DEV_DIRECT_EXIT_DELTA_gt_0": direct > EPS,
            "treatment_pf_gt_control": pf_up,
            "treatment_dd_not_worse": dd_not_worse,
            "winner_harm_dominates": winner_dominates,
            "ADDED_direct_positive": added_direct > EPS,
            "concentration_collapse": collapse,
            "concentration_warning_gt_50pct": conc_warn,
            "FWD_clear_reverse": fwd_reverse,
            "decomp_ok": decomp_ok,
        },
        "DEV_DIRECT_EXIT_DELTA": direct,
        "DEV_SLOT_RELEASE_DOWNSTREAM_DELTA": slot,
        "DEV_TOTAL_CAUSAL_DELTA": total,
        "FWD_DIRECT_EXIT_DELTA": fwd_direct,
        "FWD_SLOT_RELEASE_DOWNSTREAM_DELTA": fwd.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
        "FWD_TOTAL_CAUSAL_DELTA": fwd_total,
    }

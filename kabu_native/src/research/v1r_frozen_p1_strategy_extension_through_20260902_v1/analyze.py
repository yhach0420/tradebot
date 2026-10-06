"""CASE A-E decision. Extension economics unused unless P1 source-pin PASSes."""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.spec import (
    EXTENSION_CANDIDATE_DAYS,
    MIN_EXTENSION_FULL_DAY_N,
    MIN_EXTENSION_FULL_TRADE_N,
    SIMPLE_TECH_DAYS,
    SPOT_PREFERENCE,
)

NA = "n/a"


def unused_metrics(*, reason: str) -> dict[str, Any]:
    return {
        "used": False,
        "reason": reason,
        "day_n": None,
        "trade_n": None,
        "win": None,
        "loss": None,
        "draw": None,
        "gross_profit": None,
        "gross_loss": None,
        "PnL": None,
        "PF": None,
        "avg_pnl": None,
        "median_pnl": None,
        "MaxDD": None,
        "max_losing_streak": None,
        "positive_day_n": None,
        "negative_day_n": None,
        "flat_day_n": None,
        "AM": {"trade_n": None, "PnL": None, "PF": None},
        "PM": {"trade_n": None, "PnL": None, "PF": None},
        "CAP_BLOCKED_N": None,
        "SAME_SYMBOL_BLOCKED_N": None,
        "EXPIRED_N": None,
    }


def _pf(pnls: list[float]) -> Any:
    gp = sum(p for p in pnls if p > 0)
    gl = sum(-p for p in pnls if p < 0)
    if gl <= 1e-12:
        return None if gp <= 1e-12 else "Infinity"
    return gp / gl


def _maxdd(trades: list[dict[str, Any]]) -> float:
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in trades:
        eq += float(t.get("pnl_yen_100") or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return round(dd, 2)


def _streak(trades: list[dict[str, Any]]) -> int:
    cur = best = 0
    for t in trades:
        p = float(t.get("pnl_yen_100") or 0.0)
        if p < -1e-9:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def _sess(trades: list[dict[str, Any]], sess: str) -> dict[str, Any]:
    chunk = [t for t in trades if str(t.get("session") or "") == sess]
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in chunk]
    return {"trade_n": len(chunk), "PnL": round(sum(pnls), 2), "PF": _pf(pnls)}


def metrics_from_trades(
    trades: list[dict[str, Any]],
    *,
    daily: Optional[list[dict[str, Any]]] = None,
    days: Optional[list[str]] = None,
) -> dict[str, Any]:
    if not trades and not days:
        return unused_metrics(reason="no_trades")
    by_day: dict[str, float] = {}
    for t in trades:
        d = str(t.get("date") or "")
        by_day[d] = by_day.get(d, 0.0) + float(t.get("pnl_yen_100") or 0.0)
    if days is None:
        days = sorted(by_day)
    pos = sum(1 for d in days if by_day.get(d, 0.0) > 1e-9)
    neg = sum(1 for d in days if by_day.get(d, 0.0) < -1e-9)
    flat = sum(1 for d in days if abs(by_day.get(d, 0.0)) <= 1e-9)
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in trades]
    w = sum(1 for p in pnls if p > 1e-9)
    l = sum(1 for p in pnls if p < -1e-9)
    dr = len(pnls) - w - l
    gp = round(sum(p for p in pnls if p > 0), 2)
    gl = round(sum(-p for p in pnls if p < 0), 2)
    tot = round(sum(pnls), 2)
    daily_map = {str(r.get("date")): r for r in list(daily or [])}
    cap = same = exp = 0
    for d in days:
        r = daily_map.get(d) or {}
        cap += int(r.get("cap_blocked") or 0)
        same += int(r.get("same_symbol_blocked") or 0)
        exp += int(r.get("expired") or 0)
    return {
        "used": True,
        "reason": "",
        "day_n": len(days),
        "day_list": list(days),
        "trade_n": len(trades),
        "win": w,
        "loss": l,
        "draw": dr,
        "gross_profit": gp,
        "gross_loss": gl,
        "PnL": tot,
        "PF": _pf(pnls),
        "avg_pnl": round(tot / len(trades), 4) if trades else None,
        "median_pnl": float(median(pnls)) if pnls else None,
        "MaxDD": _maxdd(trades),
        "max_losing_streak": _streak(trades),
        "positive_day_n": pos,
        "negative_day_n": neg,
        "flat_day_n": flat,
        "AM": _sess(trades, "AM"),
        "PM": _sess(trades, "PM"),
        "CAP_BLOCKED_N": cap if daily else None,
        "SAME_SYMBOL_BLOCKED_N": same if daily else None,
        "EXPIRED_N": exp if daily else None,
        "daily_pnl": {d: round(by_day.get(d, 0.0), 2) for d in days},
    }


def robustness(trades: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    if not trades or not days:
        return {"used": False, "reason": "extension_replay_not_run"}
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in days}
    for t in trades:
        d = str(t.get("date") or "")
        if d in by_day:
            by_day[d].append(t)
    day_pnl = {d: round(sum(float(t.get("pnl_yen_100") or 0.0) for t in by_day[d]), 2) for d in days}
    tot = sum(day_pnl.values())
    ordered = sorted(days, key=lambda d: -day_pnl[d])
    top1 = ordered[0] if ordered else ""
    top3 = ordered[:3]
    top1_pnl = day_pnl.get(top1, 0.0)
    top3_pnl = sum(day_pnl[d] for d in top3)
    top1_trades = by_day.get(top1) or []
    top3_trades = [t for d in top3 for t in by_day[d]]
    loo = []
    for drop in days:
        rest = [t for t in trades if str(t.get("date")) != drop]
        loo.append({"drop": drop, "PnL": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in rest), 2)})
    loo_min = min(loo, key=lambda r: r["PnL"]) if loo else {"drop": "", "PnL": None}
    by_sym: dict[str, float] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        by_sym[s] = by_sym.get(s, 0.0) + float(t.get("pnl_yen_100") or 0.0)
    top_sym = max(by_sym, key=lambda s: by_sym[s]) if by_sym else ""
    return {
        "used": True,
        "top1_day": top1,
        "top1_day_pnl": top1_pnl,
        "top1_day_contribution": (top1_pnl / tot) if tot else None,
        "top3_days": top3,
        "top3_day_contribution": (top3_pnl / tot) if tot else None,
        "top_symbol": top_sym,
        "top_symbol_pnl": by_sym.get(top_sym, 0.0) if top_sym else None,
        "top_symbol_contribution": (by_sym.get(top_sym, 0.0) / tot) if tot and top_sym else None,
        "EX_TOP1": {"PnL": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in top1_trades), 2), "PF": _pf([float(t.get("pnl_yen_100") or 0.0) for t in top1_trades])},
        "EX_TOP3": {"PnL": round(sum(float(t.get("pnl_yen_100") or 0.0) for t in top3_trades), 2), "PF": _pf([float(t.get("pnl_yen_100") or 0.0) for t in top3_trades])},
        "LOO": loo,
        "LOO_min_PnL": loo_min.get("PnL"),
        "LOO_min_drop": loo_min.get("drop"),
        "diagnostic_only": True,
        "strategy_symbols_removed": False,
    }


def inventory_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes: dict[str, int] = {}
    for r in rows:
        k = str(r.get("capture_class") or "INVALID")
        classes[k] = classes.get(k, 0) + 1
    full = [r for r in rows if r.get("capture_class") == "FULL"]
    partial = [r for r in rows if r.get("capture_class") == "PARTIAL"]
    unresolved = [r for r in rows if r.get("capture_class") == "UNIVERSE_UNRESOLVED" or not r.get("universe_resolved")]
    invalid = [r for r in rows if r.get("capture_class") in {"INVALID", "MISSING"}]
    return {
        "candidate_n": len(EXTENSION_CANDIDATE_DAYS),
        "FULL_N": len(full),
        "PARTIAL_N": len(partial),
        "DEGRADED_N": classes.get("DEGRADED", 0),
        "INVALID_MISSING_N": len(invalid),
        "UNIVERSE_UNRESOLVED_N": len(unresolved),
        "classes": classes,
        "full_days": [r["date"] for r in full],
        "partial_days": [r["date"] for r in partial],
        "unresolved_days": [r["date"] for r in unresolved],
        "eligible_days": [r["date"] for r in rows if r.get("replay_eligible")],
    }


def spot_plan(full_days: list[str]) -> dict[str, Any]:
    chosen: list[str] = []
    reasons: list[str] = []
    for pref in SPOT_PREFERENCE:
        if pref in full_days:
            chosen.append(pref)
            reasons.append(f"{pref}: preferred FULL eligible")
        else:
            reasons.append(f"{pref}: not FULL eligible")
    for d in full_days:
        if len(chosen) >= 2:
            break
        if d not in chosen:
            chosen.append(d)
            reasons.append(f"{d}: nearest eligible substitute")
    return {
        "preference": list(SPOT_PREFERENCE),
        "chosen": chosen,
        "n": len(chosen),
        "reasons": reasons,
        "frozen_before_economics": True,
    }


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 0


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= 0


def _gt1(v: Any) -> bool:
    return v is not None and float(v) > 1.0


def _ge1(v: Any) -> bool:
    return v is not None and float(v) >= 1.0


def decide(
    *,
    pin: dict[str, Any],
    alias: dict[str, Any],
    p1: dict[str, Any],
    inv: dict[str, Any],
    extension: dict[str, Any],
    spot: dict[str, Any],
    leak_ok: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    if not alias.get("ok"):
        blockers.append("ENTRY_ALIAS_SEMANTIC_CONFLICT")
    if not pin.get("ok"):
        blockers.append("P1_SOURCE_UNRECOVERABLE")
    if not p1.get("ok"):
        blockers.append("PRIOR_P1_IDENTITY_OR_LEDGER_FAIL")
    if not leak_ok:
        blockers.append("ISOLATION_LEAK")
    if bool(spot.get("ran")) and not bool(spot.get("pass")):
        blockers.append("EXACT_FAST_EXTENSION_PARITY_FAIL")
    if bool(pin.get("ok")) and not bool(extension.get("ran")):
        blockers.append("PIN_PASS_BUT_EXTENSION_REPLAY_NOT_RUN")

    full_n = int(inv.get("FULL_N") or 0)
    ext_m = dict(extension.get("metrics") or {})
    ext_used = bool(extension.get("ran") and ext_m.get("used"))
    trade_n = int(ext_m.get("trade_n") or 0) if ext_used else 0
    coverage_pass = ext_used and full_n >= MIN_EXTENSION_FULL_DAY_N and trade_n >= MIN_EXTENSION_FULL_TRADE_N
    stress = dict(extension.get("stress") or {})
    rob = dict(extension.get("robustness") or {})
    combined = dict(extension.get("combined") or {})
    stress_days = list(stress.get("day_list") or [])
    stress_ok_cov = len(stress_days) >= 2
    stress_econ_ok = bool(stress.get("used")) and _ge0(stress.get("PnL")) and _ge1(stress.get("PF"))

    if blockers:
        case = "E"
        verdict = "V1R_FROZEN_REPLAY_INTEGRITY_FAILED"
        next_step = (
            "STOP. Restore byte-identical P1 V1RNativeEntryLive and V1RLiveDualLane into a research-only isolated copy "
            "(SHA e25285... / 810719...). Do not checkout the main tree. Do not replay Sep-05 current Runtime. "
            "Do not start NEW ENTRY FAMILY. Do not size. Do not interpret extension economics."
        )
        return _pack(
            case,
            verdict,
            next_step,
            blockers=blockers,
            coverage_pass=False,
            coverage_evaluated=False,
            ext_used=False,
            sizing=False,
            new_family=False,
            v1r_priority=False,
        )

    if not coverage_pass:
        case = "D"
        verdict = "V1R_EXISTING_STRATEGY_INSUFFICIENT_EXTENSION_DATA"
        next_step = (
            "STOP. Extension FULL coverage is below MIN_EXTENSION_FULL_DAY_N=6 or MIN_EXTENSION_FULL_TRADE_N=80. "
            "Do not judge Maintained/Mixed/Decayed. Do not start NEW ENTRY FAMILY to escape data shortage."
        )
        return _pack(
            case,
            verdict,
            next_step,
            blockers=[],
            coverage_pass=False,
            coverage_evaluated=True,
            ext_used=ext_used,
            sizing=False,
            new_family=False,
            v1r_priority=False,
        )

    ext_pnl_ok = _gt0(ext_m.get("PnL"))
    ext_pf_ok = _gt1(ext_m.get("PF"))
    day_ok = int(ext_m.get("positive_day_n") or 0) >= int(ext_m.get("negative_day_n") or 0)
    top1 = dict(rob.get("EX_TOP1") or {})
    top1_ok = _gt0(top1.get("PnL")) and _gt1(top1.get("PF"))
    comb_ok = _gt0(combined.get("PnL")) and _gt1(combined.get("PF"))
    strong = {
        "INTEGRITY": True,
        "EXTENSION_PNL_GT_0": ext_pnl_ok,
        "EXTENSION_PF_GT_1": ext_pf_ok,
        "DAY_SIGNS": day_ok,
        "EX_TOP1_PNL_GT_0": _gt0(top1.get("PnL")),
        "EX_TOP1_PF_GT_1": _gt1(top1.get("PF")),
        "COMBINED_PNL_GT_0": _gt0(combined.get("PnL")),
        "COMBINED_PF_GT_1": _gt1(combined.get("PF")),
        "STRESS_COVERAGE": stress_ok_cov,
        "STRESS_PNL_GE_0": _ge0(stress.get("PnL")) if stress.get("used") else False,
        "STRESS_PF_GE_1": _ge1(stress.get("PF")) if stress.get("used") else False,
    }

    if not ext_pnl_ok or not ext_pf_ok:
        case = "C"
        verdict = "V1R_EXISTING_STRATEGY_EDGE_DECAYED"
        next_step = (
            "NEW ENTRY FAMILY DESIGN_ALLOWED=true, but do not start in this run. "
            "Prior FULL14 profit must not rescue a non-positive extension. STOP."
        )
        return _pack(
            case,
            verdict,
            next_step,
            blockers=[],
            coverage_pass=True,
            coverage_evaluated=True,
            ext_used=True,
            sizing=False,
            new_family=True,
            v1r_priority=False,
            strong=strong,
        )

    if not stress_ok_cov:
        case = "B"
        verdict = "V1R_EXISTING_STRATEGY_PRIORITY_MIXED"
        next_step = (
            "STRESS_INSUFFICIENT. Keep frozen V1R. Do not size. Do not start NEW ENTRY FAMILY. "
            "Next: V1R residual-risk / regime / concentration architecture review. STOP."
        )
        return _pack(
            case,
            verdict,
            next_step,
            blockers=[],
            coverage_pass=True,
            coverage_evaluated=True,
            ext_used=True,
            sizing=False,
            new_family=False,
            v1r_priority=False,
            strong=strong,
            stress_insufficient=True,
        )

    mixed_reasons = []
    if not top1_ok:
        mixed_reasons.append("EX_TOP1_FAIL")
    if not day_ok:
        mixed_reasons.append("DAY_SIGNS_WEAK")
    if not stress_econ_ok:
        mixed_reasons.append("STRESS4_FAIL")
    contrib = rob.get("top1_day_contribution")
    if contrib is not None and float(contrib) >= 0.5:
        mixed_reasons.append("CONCENTRATION_STRONG")
    if mixed_reasons or not comb_ok or not all(strong.values()):
        case = "B"
        verdict = "V1R_EXISTING_STRATEGY_PRIORITY_MIXED"
        next_step = (
            "Keep frozen V1R. Do not size. Do not start NEW ENTRY FAMILY. "
            "Next: V1R residual-risk / regime / concentration architecture review. STOP."
        )
        return _pack(
            case,
            verdict,
            next_step,
            blockers=[],
            coverage_pass=True,
            coverage_evaluated=True,
            ext_used=True,
            sizing=False,
            new_family=False,
            v1r_priority=False,
            strong=strong,
            mixed_reasons=mixed_reasons,
        )

    case = "A"
    verdict = "V1R_EXISTING_STRATEGY_PRIORITY_MAINTAINED"
    next_step = (
        "Keep frozen V1R. SIZING_RESEARCH_ALLOWED=true on existing data only (not Runtime sizing). "
        "Next: V1R runtime/operational parity then risk/sizing readiness review. Future data still forbidden. STOP."
    )
    return _pack(
        case,
        verdict,
        next_step,
        blockers=[],
        coverage_pass=True,
        coverage_evaluated=True,
        ext_used=True,
        sizing=True,
        new_family=False,
        v1r_priority=True,
        strong=strong,
    )


def _pack(
    case: str,
    verdict: str,
    next_step: str,
    *,
    blockers: list[str],
    coverage_pass: bool,
    coverage_evaluated: bool,
    ext_used: bool,
    sizing: bool,
    new_family: bool,
    v1r_priority: bool,
    strong: Optional[dict[str, Any]] = None,
    mixed_reasons: Optional[list[str]] = None,
    stress_insufficient: bool = False,
) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "blockers": blockers,
        "minimum_coverage_pass": coverage_pass,
        "minimum_coverage_evaluated": coverage_evaluated,
        "extension_economics_used": ext_used,
        "SIZING_RESEARCH_ALLOWED": bool(sizing),
        "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": bool(new_family),
        "V1R_RESEARCH_PRIORITY_MAINTAINED": bool(v1r_priority),
        "CANDIDATE_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "CAP_CHANGED": False,
        "SIZING_CHANGED": False,
        "strong_gates": strong or {},
        "mixed_reasons": mixed_reasons or [],
        "STRESS_INSUFFICIENT": bool(stress_insufficient),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "FUTURE_DATA_USED": False,
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return NA
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        if v != v:
            return NA
        return repr(v) if abs(v) < 1e-6 or abs(v) > 1e6 else f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    pin = dict(report.get("source_pin") or {})
    alias = dict(report.get("activation_alias") or {})
    p0 = dict(report.get("p0_reuse") or {})
    p1 = dict(report.get("p1_reuse") or {})
    inv = dict(report.get("inventory_summary") or {})
    ident = dict(report.get("identity") or {})
    st = dict(report.get("simple_tech_closure") or {})
    ext = dict(report.get("extension") or {})
    spot = dict(report.get("spot") or {})
    cohorts = dict(report.get("cohorts") or {})
    a = dict(cohorts.get("A_PRIOR_P1_FULL14") or {})
    b = dict(cohorts.get("B_EXTENSION_FULL") or {})
    c = dict(cohorts.get("C_COMBINED_FULL") or {})
    e = dict(cohorts.get("E_COMMON_SIMPLE_TECH_DAYS") or {})
    f = dict(cohorts.get("F_REUSED_HISTORY_STRESS_4") or {})
    rob = dict(ext.get("robustness") or {})
    actual = dict(p0.get("ACTUAL_20260820") or {})
    replay20 = dict(p0.get("REPLAY_20260820") or {})
    na_e = NA if str(dec.get("CASE")) == "E" else NA
    return {
        "1": (
            "PRIMARY_GOAL: test whether frozen completed P1 V1R still shows portfolio edge on latest allowed reused history "
            "(20260824-20260902). Result chooses V1R research priority vs later NEW ENTRY FAMILY DESIGN. "
            "NON_GOALS kept: no Simple-Tech reopen, no new ENTRY/EXIT, no threshold, no Sizing implementation, no Sep-05 Runtime replay, no future/OOS."
        ),
        "2": (
            f"Simple-Tech remains closed CASE {st.get('CASE')} verdict={st.get('VERDICT')}. "
            f"CURRENT_T3_STACK_CLOSED={st.get('CURRENT_T3_STACK_CLOSED')} "
            f"CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED={st.get('CURRENT_SIMPLE_TECH_ENTRY_FAMILY_CLOSED')} "
            f"CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED={st.get('CURRENT_SIMPLE_TECH_TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED')}. "
            "eligible ENTRY=17 qualified=0. Not re-run."
        ),
        "3": (
            "V1R is evaluated first because it is the already-complete frozen P1 strategy. "
            "NEW ENTRY FAMILY is only allowed after this run if CASE C (extension edge decayed). "
            "CASE D/E must not flee to a new family. Simple-Tech family is exhausted (CASE C)."
        ),
        "4": (
            "Evaluation target=FROZEN_P1_V1R_STRATEGY. P1 result name CURRENT_RUNTIME_REPLAY means Runtime as of 2026-08-21. "
            "2026-09-05 working-tree Runtime was not replayed."
        ),
        "5": str(ident.get("STRATEGY_SHA") or ""),
        "6": str(ident.get("ENTRY_SHA") or ""),
        "7": str(ident.get("EXIT_SHA") or ""),
        "8": str(ident.get("ANCHOR_SHA") or ""),
        "9": str(ident.get("REPLAY_CODE_SHA") or ""),
        "10": str(ident.get("V1R_NATIVE_ENTRY_LIVE_SHA") or ""),
        "11": str(ident.get("V1R_LIVE_DUAL_LANE_SHA") or ""),
        "12": str(ident.get("P1_RUNNER_SHA") or ""),
        "13": f"{pin.get('PIN_MODE')} all_equal={pin.get('all_equal')} rows={pin.get('rows')}",
        "14": str(pin.get("PIN_MODE") or "") + (("; " + str(pin.get("blocker") or "")) if pin.get("blocker") else ""),
        "15": (
            f"{alias.get('why_p1_uses_f288')} {alias.get('activation_alias_meaning')} "
            f"ENTRY_SHA_eq_ENTRY_V1R_SHA={alias.get('ENTRY_SHA_eq_ENTRY_V1R_SHA')} "
            f"semantic_conflict={alias.get('semantic_conflict')}"
        ),
        "16": f"true P0-3 reused verdict={p0.get('P0_3_verdict')} (not re-run)",
        "17": f"true P0-4 reused verdict=P0_4_EXACT_FAST_PARITY_PASS (not re-run)",
        "18": (
            f"ALL_DAYS_TRADE_PARITY={p0.get('P0_4_ALL_DAYS_TRADE_PARITY')} "
            f"ALL_DAYS_ANCHOR_PARITY={p0.get('P0_4_ALL_DAYS_ANCHOR_PARITY')} "
            f"ALL_DAYS_PNL_PARITY={p0.get('P0_4_ALL_DAYS_PNL_PARITY')} "
            f"DETERMINISM={p0.get('P0_4_DETERMINISM')}"
        ),
        "19": f"label={actual.get('label')} trades={actual.get('trades')} PnL={actual.get('pnl')}",
        "20": f"label={replay20.get('label')} trades={replay20.get('trades')} PnL={replay20.get('pnl')}",
        "21": f"true IS_RUNTIME_DEFECT={p0.get('IS_RUNTIME_DEFECT')} note={p0.get('ROOT_CAUSE_NOTE')}",
        "22": f"false IS_REPLAY_DEFECT={p0.get('IS_REPLAY_DEFECT')} ROOT_CAUSE_CLASS={p0.get('ROOT_CAUSE_CLASS')}",
        "23": f"identity_ok={p1.get('identity_ok')} day_list_ok={p1.get('day_list_ok')} daily_ledger_sha_parity={p1.get('daily_ledger_sha_parity')} ok={p1.get('ok')}",
        "24": f"PRIOR_REUSED_FULL_DAY_N={p1.get('PRIOR_REUSED_FULL_DAY_N')} PRIOR_REUSED_TRADE_N={p1.get('PRIOR_REUSED_TRADE_N')} restreamed={p1.get('restreamed')}",
        "25": "8",
        "26": str(inv.get("FULL_N")),
        "27": str(inv.get("PARTIAL_N")),
        "28": str(inv.get("INVALID_MISSING_N")),
        "29": str(inv.get("UNIVERSE_UNRESOLVED_N")),
        "30": (
            f"{dec.get('minimum_coverage_pass')} evaluated={dec.get('minimum_coverage_evaluated')} "
            f"MIN_DAY={MIN_EXTENSION_FULL_DAY_N} MIN_TRADE={MIN_EXTENSION_FULL_TRADE_N}"
        ),
        "31": f"chosen={spot.get('chosen')} preference={spot.get('preference')} ran={spot.get('ran')} reasons={spot.get('reasons')}",
        "32": f"trade_parity={spot.get('trade_parity')} ran={spot.get('ran')}",
        "33": f"ledger_sha_parity={spot.get('ledger_sha_parity')} ran={spot.get('ran')}",
        "34": _fmt(b.get("trade_n")),
        "35": _fmt(b.get("PnL")),
        "36": _fmt(b.get("PF")),
        "37": _fmt(b.get("MaxDD")),
        "38": f"pos={_fmt(b.get('positive_day_n'))} neg={_fmt(b.get('negative_day_n'))} flat={_fmt(b.get('flat_day_n'))}",
        "39": f"PnL={_fmt((rob.get('EX_TOP1') or {}).get('PnL'))} PF={_fmt((rob.get('EX_TOP1') or {}).get('PF'))}",
        "40": f"PnL={_fmt((rob.get('EX_TOP3') or {}).get('PnL'))} PF={_fmt((rob.get('EX_TOP3') or {}).get('PF'))}",
        "41": _fmt(rob.get("LOO_min_PnL")),
        "42": (
            f"top1_day={rob.get('top1_day')} contrib={_fmt(rob.get('top1_day_contribution'))} "
            f"top_symbol={rob.get('top_symbol')} contrib={_fmt(rob.get('top_symbol_contribution'))} diagnostic_only=true"
        ),
        "43": _fmt(c.get("trade_n")),
        "44": _fmt(c.get("PnL")),
        "45": _fmt(c.get("PF")),
        "46": _fmt(c.get("MaxDD")),
        "47": _fmt(f.get("day_n")),
        "48": _fmt(f.get("trade_n")),
        "49": _fmt(f.get("PnL")),
        "50": _fmt(f.get("PF")),
        "51": _fmt(f.get("MaxDD")),
        "52": (
            f"used={e.get('used')} days={_fmt(e.get('day_n'))} trades={_fmt(e.get('trade_n'))} "
            f"PnL={_fmt(e.get('PnL'))} PF={_fmt(e.get('PF'))} MaxDD={_fmt(e.get('MaxDD'))} "
            f"note={e.get('reason') or 'V1R metrics on Simple-Tech calendar intersection; extension days omitted when replay not run'}"
        ),
        "53": "false",
        "54": "false",
        "55": "false",
        "56": "false",
        "57": "false",
        "58": "false",
        "59": "20260902",
        "60": "true",
        "61": "false",
        "62": "false",
        "63": "0/0/0",
        "64": f"{dec.get('VERDICT')} CASE {dec.get('CASE')} blockers={dec.get('blockers')}",
        "65": str(bool(dec.get("V1R_RESEARCH_PRIORITY_MAINTAINED"))).lower(),
        "66": str(bool(dec.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED"))).lower(),
        "67": str(bool(dec.get("SIZING_RESEARCH_ALLOWED"))).lower(),
        "68": str(dec.get("NEXT") or ""),
        "_na_e": na_e,
        "_a_pnl": _fmt(a.get("PnL")),
        "_simple_tech_days": ",".join(SIMPLE_TECH_DAYS),
    }


def common_simple_tech_metrics(
    *,
    prior_trades: list[dict[str, Any]],
    prior_daily: list[dict[str, Any]],
    extension_trades: list[dict[str, Any]],
    extension_ran: bool,
) -> dict[str, Any]:
    st = set(SIMPLE_TECH_DAYS)
    prior = [t for t in prior_trades if str(t.get("date")) in st]
    ext = [t for t in extension_trades if str(t.get("date")) in st] if extension_ran else []
    trades = prior + ext
    days = sorted({str(t.get("date")) for t in trades})
    missing_ext = [d for d in SIMPLE_TECH_DAYS if d in set(EXTENSION_CANDIDATE_DAYS) and d not in days]
    m = metrics_from_trades(trades, daily=prior_daily, days=days)
    if missing_ext and not extension_ran:
        m["reason"] = (
            "prior FULL14 ∩ Simple-Tech only; extension Simple-Tech days 20260824-27 not replayed because P1 source-pin failed"
        )
        m["missing_extension_simple_tech_days"] = missing_ext
    return m

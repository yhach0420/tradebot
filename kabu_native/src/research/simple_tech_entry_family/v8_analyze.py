"""V8 arm metrics, pairwise role gates, architecture cases. No extra arms. No thresholds."""
from __future__ import annotations

from typing import Any, Optional

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v3_analyze import concentration, day_rows, day_sign_counts, horizon_pack
from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v8_spec import ROLE_MIN_EXECUTABLE_N


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _gt(a: Any, b: Any) -> bool:
    return a is not None and b is not None and float(a) > float(b)


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= 0.0


def arm_metrics(funnel: dict[str, Any], days: list[str]) -> dict[str, Any]:
    exe = list(funnel.get("exe_rows") or [])
    pack = horizon_pack(exe)
    daily = day_rows(exe, days)
    signs = {h: day_sign_counts(daily, f"MARKOUT{h}_MEAN") for h in (60, 180, 300)}
    conc = {h: concentration(exe, f"markout_{h}") for h in (60, 180, 300)}
    syms = {h: symbol_pack(exe, f"markout_{h}") for h in (60, 180, 300)}
    out: dict[str, Any] = {
        "ARM_ID": funnel.get("ARM_ID"),
        "SIGNAL_N": funnel.get("SIGNAL_N"),
        "EXECUTABLE_SIGNAL_N": funnel.get("EXECUTABLE_SIGNAL_N"),
        "horizon": pack,
        "daily": daily,
        "day_sign": signs,
        "concentration": conc,
        "symbol": syms,
        "POSITIVE_DAY_N_180": signs[180].get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_180": signs[180].get("NEGATIVE_DAY_N"),
        "POSITIVE_DAY_N_300": signs[300].get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N_300": signs[300].get("NEGATIVE_DAY_N"),
        "EX_BEST_180": conc[180].get("EX_BEST_DAY_MARKOUT"),
        "EX_BEST_300": conc[300].get("EX_BEST_DAY_MARKOUT"),
        "EX_TOP3_180": conc[180].get("EX_TOP3_DAY_MARKOUT"),
        "EX_TOP3_300": conc[300].get("EX_TOP3_DAY_MARKOUT"),
        "DROP_TOP_SYMBOL_180": syms[180].get("DROP_TOP_SYMBOL_MARKOUT"),
        "DROP_TOP_SYMBOL_300": syms[300].get("DROP_TOP_SYMBOL_MARKOUT"),
        "TOP_SYMBOL": syms[180].get("TOP_SYMBOL"),
        "MFE_MEAN": pack.get("MFE_MEAN"),
        "MFE_MEDIAN": pack.get("MFE_MEDIAN"),
        "MAE_MEAN": pack.get("MAE_MEAN"),
        "MAE_MEDIAN": pack.get("MAE_MEDIAN"),
        "COST_RECOVERY_300": pack.get("COST_RECOVERY_RATE_300"),
        "exe_rows": exe,
    }
    for h in (60, 180, 300):
        out[f"MARKOUT{h}_MEAN"] = pack.get(f"MARKOUT_{h}_MEAN")
        out[f"MARKOUT{h}_MEDIAN"] = pack.get(f"MARKOUT_{h}_MEDIAN")
        out[f"MARKOUT{h}_POS_RATE"] = pack.get(f"MARKOUT_{h}_POS_RATE")
    return out


def slim_arm(arm: dict[str, Any]) -> dict[str, Any]:
    skip = {"exe_rows", "daily", "horizon", "day_sign", "concentration", "symbol"}
    return {k: v for k, v in arm.items() if k not in skip}


def day_improve(left: dict[str, Any], right: dict[str, Any], h: int) -> dict[str, Any]:
    mk = f"MARKOUT{h}_MEAN"
    by_l = {str(r.get("date")): r for r in (left.get("daily") or [])}
    by_r = {str(r.get("date")): r for r in (right.get("daily") or [])}
    pos = neg = zero = compared = 0
    for d, rl in by_l.items():
        if not int(rl.get("SIGNAL_N") or 0):
            continue
        rr = by_r.get(d) or {}
        if not int(rr.get("SIGNAL_N") or 0):
            continue
        a = rl.get(mk)
        b = rr.get(mk)
        if not _finite(a) or not _finite(b):
            continue
        compared += 1
        if float(a) > float(b) + 1e-12:
            pos += 1
        elif float(a) < float(b) - 1e-12:
            neg += 1
        else:
            zero += 1
    return {
        "COMPARED_DAY_N": compared,
        "IMPROVE_DAY_N": pos,
        "WORSE_DAY_N": neg,
        "TIE_DAY_N": zero,
        "MULTI_DAY_IMPROVE": bool(pos >= 2 and pos > neg),
    }


def vs_arm(left: dict[str, Any], right: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    a = _gt(left.get("MARKOUT180_MEAN"), right.get("MARKOUT180_MEAN"))
    b = _gt(left.get("MARKOUT300_MEAN"), right.get("MARKOUT300_MEAN"))
    c = _gt(left.get("MARKOUT180_MEDIAN"), right.get("MARKOUT180_MEDIAN")) and _gt(
        left.get("MARKOUT300_MEDIAN"), right.get("MARKOUT300_MEDIAN")
    )
    d180 = day_improve(left, right, 180)
    d300 = day_improve(left, right, 300)
    d = bool(d180.get("MULTI_DAY_IMPROVE") and d300.get("MULTI_DAY_IMPROVE"))
    e = _gt(left.get("EX_BEST_180"), right.get("EX_BEST_180")) and _gt(left.get("EX_BEST_300"), right.get("EX_BEST_300"))
    top = str(left.get("TOP_SYMBOL") or "")
    l_rows = left.get("exe_rows") or []
    r_rows = right.get("exe_rows") or []
    l180 = drop_symbol_mean(l_rows, top, "markout_180") if top else left.get("MARKOUT180_MEAN")
    r180 = drop_symbol_mean(r_rows, top, "markout_180") if top else right.get("MARKOUT180_MEAN")
    l300 = drop_symbol_mean(l_rows, top, "markout_300") if top else left.get("MARKOUT300_MEAN")
    r300 = drop_symbol_mean(r_rows, top, "markout_300") if top else right.get("MARKOUT300_MEAN")
    if r180 is None:
        r180 = right.get("MARKOUT180_MEAN")
    if r300 is None:
        r300 = right.get("MARKOUT300_MEAN")
    f = _gt(l180, r180) and _gt(l300, r300)
    g = int(left.get("EXECUTABLE_SIGNAL_N") or 0) >= int(ROLE_MIN_EXECUTABLE_N) and int(
        right.get("EXECUTABLE_SIGNAL_N") or 0
    ) >= int(ROLE_MIN_EXECUTABLE_N)
    h = bool(integrity_ok)
    ok = bool(a and b and c and d and e and f and g and h)
    mean_only = bool(a and b)
    substantial = bool(ok or (mean_only and bool(c) and g and h))
    return {
        "A_180_MEAN": a,
        "B_300_MEAN": b,
        "C_MEDIAN": c,
        "D_MULTI_DAY": d,
        "E_EX_BEST": e,
        "F_DROP_TOP_SYMBOL": f,
        "G_COVERAGE": g,
        "H_INTEGRITY": h,
        "ROBUST_BETTER": ok,
        "MEAN_IMPROVE_180_300": mean_only,
        "SUBSTANTIAL": substantial,
        "EFFECT_180": (
            None
            if left.get("MARKOUT180_MEAN") is None or right.get("MARKOUT180_MEAN") is None
            else float(left["MARKOUT180_MEAN"]) - float(right["MARKOUT180_MEAN"])
        ),
        "EFFECT_300": (
            None
            if left.get("MARKOUT300_MEAN") is None or right.get("MARKOUT300_MEAN") is None
            else float(left["MARKOUT300_MEAN"]) - float(right["MARKOUT300_MEAN"])
        ),
        "day_improve_180": d180,
        "day_improve_300": d300,
    }


def core_edge_gate(a4: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    cov = int(a4.get("EXECUTABLE_SIGNAL_N") or 0) >= int(ROLE_MIN_EXECUTABLE_N)
    a = _gt0(a4.get("MARKOUT60_MEAN"))
    b = _gt0(a4.get("MARKOUT180_MEAN"))
    c = _gt0(a4.get("MARKOUT300_MEAN"))
    d = _ge0(a4.get("MARKOUT180_MEDIAN"))
    e = _ge0(a4.get("MARKOUT300_MEDIAN"))
    f = int(a4.get("POSITIVE_DAY_N_180") or 0) > int(a4.get("NEGATIVE_DAY_N_180") or 0)
    g = int(a4.get("POSITIVE_DAY_N_300") or 0) > int(a4.get("NEGATIVE_DAY_N_300") or 0)
    h = _gt0(a4.get("EX_BEST_180"))
    i = _gt0(a4.get("EX_BEST_300"))
    k = bool(integrity_ok)
    ok = bool(a and b and c and d and e and f and g and h and i and cov and k)
    return {
        "A_MARKOUT_60_MEAN": a,
        "B_MARKOUT_180_MEAN": b,
        "C_MARKOUT_300_MEAN": c,
        "D_MARKOUT_180_MEDIAN": d,
        "E_MARKOUT_300_MEDIAN": e,
        "F_POS_DAYS_180": f,
        "G_POS_DAYS_300": g,
        "H_EX_BEST_180": h,
        "I_EX_BEST_300": i,
        "J_COVERAGE": cov,
        "K_INTEGRITY": k,
        "CORE_ENTRY_EDGE_SUPPORTED": ok,
    }


def _cohort(rows: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    pos = [r for r in rows if _finite(r.get("markout_180")) and float(r["markout_180"]) > 1e-12]
    neg = [r for r in rows if _finite(r.get("markout_180")) and float(r["markout_180"]) <= 1e-12]
    return {
        "feature": feat,
        "POS_N": len(pos),
        "NEG_N": len(neg),
        "POS_MEDIAN": _median([r.get(feat) for r in pos]),
        "NEG_MEDIAN": _median([r.get(feat) for r in neg]),
        "POS_MEAN": _mean([r.get(feat) for r in pos]),
        "NEG_MEAN": _mean([r.get(feat) for r in neg]),
        "POS_GT_NEG_MEDIAN": _gt(_median([r.get(feat) for r in pos]), _median([r.get(feat) for r in neg])),
        "diagnostic_only": True,
        "not_a_gate": True,
    }


def a4_diagnostics(exe: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in exe if _finite(r.get("markout_180")) and float(r["markout_180"]) > 1e-12]
    neg = [r for r in exe if _finite(r.get("markout_180")) and float(r["markout_180"]) <= 1e-12]

    def _rate(xs: list[dict[str, Any]], key: str) -> Optional[float]:
        if not xs:
            return None
        return float(sum(1 for r in xs if r.get(key)) / len(xs))

    return {
        "A4_EXECUTABLE_N": len(exe),
        "PQ3": _cohort(exe, "PQ3"),
        "VQ2": _cohort(exe, "VQ2"),
        "TQ1_EMA_SEPARATION": _cohort(exe, "TQ1"),
        "TQ2_EMA21_SLOPE": _cohort(exe, "TQ2"),
        "TREND_HARD_RATE_POS": _rate(pos, "trend"),
        "TREND_HARD_RATE_NEG": _rate(neg, "trend"),
        "EMA9_GT_EMA21_RATE_POS": _rate(pos, "ema9_gt_ema21"),
        "EMA9_GT_EMA21_RATE_NEG": _rate(neg, "ema9_gt_ema21"),
        "no_threshold": True,
    }


def decide_case(
    *,
    roles: dict[str, bool],
    board_neutral: bool,
    edge: bool,
    a4_vs_a0: dict[str, Any],
    a3_vs_a0: dict[str, Any],
    a3_vs_a4: dict[str, Any],
    a5_vs_a4: dict[str, Any],
    a6_vs_a4: dict[str, Any],
    integrity_ok: bool,
) -> dict[str, Any]:
    rci = bool(roles.get("RCI_CONFIRM_ROLE_SUPPORTED"))
    board = bool(roles.get("BOARD_VETO_ROLE_SUPPORTED"))
    pa = bool(roles.get("PRICE_ACTION_HARD_ROLE_SUPPORTED"))
    vol = bool(roles.get("VOLUME_HARD_ROLE_SUPPORTED"))
    if not integrity_ok:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": "SIMPLE_TECH_V8_INTEGRITY_FAILED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }
    if edge and rci and (board or board_neutral):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V8_CORE_ARCHITECTURE_SUPPORTED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "NONE",
            "NEXT": "Core ENTRY structural verification. EXIT still forbidden. No Runtime adoption. TRUE_OOS=false.",
        }
    if bool(a5_vs_a4.get("ROBUST_BETTER")):
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V8_RCI_HARD_CONFIRM_NOT_SUPPORTED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "RCI_HARD_CONFIRM",
            "NEXT": "Do not delete RCI from the family. Reconsider its role. No extra arms. No EXIT.",
        }
    if bool(a6_vs_a4.get("ROBUST_BETTER")):
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V8_BOARD_VETO_NOT_SUPPORTED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "BOARD_VETO",
            "NEXT": "Reassess calling Board a support. No extra arms. No EXIT.",
        }
    if bool(a3_vs_a0.get("SUBSTANTIAL")) and bool(a3_vs_a4.get("ROBUST_BETTER")):
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V8_TREND_CONTEXT_STILL_REQUIRED",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": "TREND_HARD_GATE",
            "NEXT": "Re-evaluate the meaning of the Trend condition. No extra TF. No EXIT.",
        }
    if bool(a4_vs_a0.get("SUBSTANTIAL")) and not edge:
        defic = "NO_ABSOLUTE_ENTRY_EDGE"
        if (not pa) and (not vol):
            defic = "V1_LATE_HARD_GATES_DESTROY_SIGNAL_BUT_CORE_EDGE_INSUFFICIENT"
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V8_ARCHITECTURE_IMPROVED_EDGE_STILL_INSUFFICIENT",
            "PRIMARY_ARCHITECTURE_DEFICIENCY": defic,
            "NEXT": "Pullback recovery / trigger RCA. PQ3 may be considered next as recovery architecture, not as a V8 gate. No EXIT.",
        }
    return {
        "CASE": "F",
        "VERDICT": "SIMPLE_TECH_V8_CURRENT_ARCHITECTURE_NOT_SUPPORTED",
        "PRIMARY_ARCHITECTURE_DEFICIENCY": "TREND_PULLBACK_RCI_ARCHITECTURE",
        "NEXT": "Redesign Trend->Pullback->RCI architecture. Do not add arms, PQ3 gates, Persistence, retunes, or timeframes. No EXIT.",
    }


DAYS = list(ELIGIBLE_DAYS)

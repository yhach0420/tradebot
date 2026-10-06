"""V12 execution-policy metrics. Signal-anchored primary. Unfilled count as 0. No EXIT. No extra waits."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v8_analyze import DAYS, arm_metrics, slim_arm, vs_arm
from research.simple_tech_entry_family.v9_analyze import drop_top3_mean
from research.simple_tech_entry_family.v12_spec import (
    ADVERSE_BPS_GAP,
    ADVERSE_UNFILLED_MIN_N,
    CONTROL_ARM,
    FAMILY_PAIRS,
    POLICY_ARMS,
    ROLE_MIN_FILL_N,
)

HORIZONS = (60, 180, 300)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def _ge(a: Any, b: Any) -> bool:
    return a is not None and b is not None and float(a) + 1e-12 >= float(b)


def _pctl(xs: list[Any], q: float) -> Optional[float]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return None
    return float(np.percentile(vs, q))


def _exec(row: dict[str, Any], arm_id: str) -> dict[str, Any]:
    return dict((row.get("exec") or {}).get(arm_id) or {})


def _filled(row: dict[str, Any], arm_id: str) -> bool:
    return bool(_exec(row, arm_id).get("filled"))


def _horizon_means(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS:
        out[str(h)] = _mean([r.get(f"{prefix}_{h}") for r in rows])
    return out


def _remap(
    rows: list[dict[str, Any]],
    arm_id: str,
    *,
    field: str,
    uncond: bool,
) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        ex = _exec(r, arm_id)
        filled = bool(ex.get("filled"))
        rec["filled"] = filled
        rec["passive_filled"] = filled
        rec["fill_price"] = ex.get("fill_price")
        rec["fill_t"] = ex.get("fill_t")
        rec["waited_sec"] = ex.get("waited_sec")
        rec["limit_price"] = ex.get("limit_price")
        rec["family"] = ex.get("family")
        rec["collapsed_to_bid"] = ex.get("collapsed_to_bid")
        rec["evidence"] = ex.get("evidence")
        rec["improve_vs_ask0_bps"] = ex.get("improve_vs_ask0_bps")
        rec["entry_vs_mid0_bps"] = ex.get("entry_vs_mid0_bps")
        for h in HORIZONS:
            raw = ex.get(f"{field}_{h}")
            if uncond:
                if filled:
                    rec[f"markout_{h}"] = raw
                else:
                    rec[f"markout_{h}"] = 0.0
            else:
                rec[f"markout_{h}"] = raw
        out.append(rec)
    return out


def _arm_from(rows: list[dict[str, Any]], arm_id: str, *, signal_n: int, eligible_n: int) -> dict[str, Any]:
    funnel = {
        "ARM_ID": arm_id,
        "SIGNAL_N": int(signal_n),
        "EXECUTABLE_SIGNAL_N": int(eligible_n),
        "rows": rows,
        "exe_rows": rows,
    }
    return arm_metrics(funnel, list(DAYS))


def wait_stats(filled_rows: list[dict[str, Any]]) -> dict[str, Any]:
    waits = [r.get("waited_sec") for r in filled_rows]
    return {
        "WAIT_TO_FILL_MEAN": _mean(waits),
        "WAIT_TO_FILL_MEDIAN": _median(waits),
        "WAIT_TO_FILL_P75": _pctl(waits, 75.0),
        "WAIT_TO_FILL_MAX": (max(float(x) for x in waits if _finite(x)) if any(_finite(x) for x in waits) else None),
    }


def gross_split(exe: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    filled = [r for r in exe if _filled(r, arm_id)]
    unfilled = [r for r in exe if not _filled(r, arm_id)]
    out = {
        "FILLED_N": len(filled),
        "UNFILLED_N": len(unfilled),
        "FILLED_GROSS_MID": _horizon_means(filled, "gross"),
        "UNFILLED_GROSS_MID": _horizon_means(unfilled, "gross"),
    }
    return out


def extreme_adverse(gross: dict[str, Any]) -> bool:
    fn = int(gross.get("FILLED_N") or 0)
    un = int(gross.get("UNFILLED_N") or 0)
    if fn < int(ROLE_MIN_FILL_N) or un < int(ADVERSE_UNFILLED_MIN_N):
        return False
    fg = gross.get("FILLED_GROSS_MID") or {}
    ug = gross.get("UNFILLED_GROSS_MID") or {}
    f180, f300 = fg.get("180"), fg.get("300")
    u180, u300 = ug.get("180"), ug.get("300")
    if not all(_finite(x) for x in (f180, f300, u180, u300)):
        return False
    gap = float(ADVERSE_BPS_GAP)
    missed_good = _gt0(u180) and _gt0(u300)
    cream = (float(u180) > float(f180) + gap) and (float(u300) > float(f300) + gap)
    return bool(missed_good and cream)


def paired_not_worse(exe: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    filled = [r for r in exe if _filled(r, arm_id)]
    out: dict[str, Any] = {"FILLED_PAIRED_N": 0}
    ok_180 = True
    ok_300 = True
    for h in HORIZONS:
        pol: list[float] = []
        e0: list[float] = []
        for r in filled:
            pv = _exec(r, arm_id).get(f"prim_mid_{h}")
            ev = _exec(r, CONTROL_ARM).get(f"prim_mid_{h}")
            if _finite(pv) and _finite(ev):
                pol.append(float(pv))
                e0.append(float(ev))
        rec = {
            "N": len(pol),
            "POLICY_MEAN": float(np.mean(pol)) if pol else None,
            "E0_SAME_NAMES_MEAN": float(np.mean(e0)) if e0 else None,
        }
        rec["NOT_WORSE"] = bool(pol) and _ge(rec["POLICY_MEAN"], rec["E0_SAME_NAMES_MEAN"])
        out[str(h)] = rec
        if h == 180:
            ok_180 = bool(rec["NOT_WORSE"])
            out["FILLED_PAIRED_N"] = len(pol)
        if h == 300:
            ok_300 = bool(rec["NOT_WORSE"])
    out["NOT_WORSE_180_300"] = bool(ok_180 and ok_300)
    return out


def policy_metrics(
    exe: list[dict[str, Any]],
    arm_id: str,
    *,
    signal_n: int,
    e0_uncond: dict[str, Any] | None,
    integrity_ok: bool,
) -> dict[str, Any]:
    eligible = list(exe)
    mapped_all = _remap(eligible, arm_id, field="prim_mid", uncond=False)
    filled_rows = [r for r in mapped_all if r.get("filled")]
    unfilled_n = len(eligible) - len(filled_rows)
    cond_mid = _arm_from(filled_rows, arm_id, signal_n=signal_n, eligible_n=len(filled_rows))
    uncond_mid_rows = _remap(eligible, arm_id, field="prim_mid", uncond=True)
    uncond_mid = _arm_from(uncond_mid_rows, arm_id, signal_n=signal_n, eligible_n=len(eligible))
    cond_bid = _arm_from(_remap(filled_rows, arm_id, field="sec_bid", uncond=False), arm_id, signal_n=signal_n, eligible_n=len(filled_rows))
    uncond_bid = _arm_from(_remap(eligible, arm_id, field="sec_bid", uncond=True), arm_id, signal_n=signal_n, eligible_n=len(eligible))
    ft_rows = _remap(filled_rows, arm_id, field="ft_mid", uncond=False)
    ft_arm = _arm_from(ft_rows, arm_id, signal_n=signal_n, eligible_n=len(filled_rows))
    waits = wait_stats(filled_rows)
    gross = gross_split(eligible, arm_id)
    paired = paired_not_worse(eligible, arm_id)
    vs = vs_arm(uncond_mid, e0_uncond, integrity_ok=integrity_ok) if e0_uncond is not None else {}
    filled_n = len(filled_rows)
    coverage_ok = filled_n >= int(ROLE_MIN_FILL_N)
    evidence_limited = not coverage_ok
    adverse = extreme_adverse(gross)
    mean_better = bool(vs.get("A_180_MEAN") and vs.get("B_300_MEAN")) if vs else False
    mechanism_member = bool(
        coverage_ok
        and mean_better
        and paired.get("NOT_WORSE_180_300")
        and (not adverse)
        and bool(vs.get("D_MULTI_DAY"))
        and bool(vs.get("E_EX_BEST"))
        and bool(vs.get("F_DROP_TOP_SYMBOL"))
        and bool(integrity_ok)
    )
    fam = None
    wait_budget = None
    for aid, family, wait in POLICY_ARMS:
        if aid == arm_id:
            fam = family
            wait_budget = float(wait)
            break
    if arm_id == CONTROL_ARM:
        fam = "CROSS"
        wait_budget = 0.0
        mechanism_member = False
    out = {
        "ARM_ID": arm_id,
        "FAMILY": fam,
        "SIGNAL_N": int(signal_n),
        "ELIGIBLE_N": len(eligible),
        "FILLED_N": filled_n,
        "UNFILLED_N": unfilled_n,
        "FILL_RATE": (float(filled_n) / float(len(eligible)) if eligible else None),
        "DELAY_BUDGET_SEC": wait_budget,
        "DELAY_SEC": waits.get("WAIT_TO_FILL_MEAN"),
        **waits,
        "ENTRY_PRICE_IMPROVEMENT_VS_ASK0_BPS": _mean([r.get("improve_vs_ask0_bps") for r in filled_rows]),
        "ENTRY_PRICE_VS_MID0_BPS": _mean([r.get("entry_vs_mid0_bps") for r in filled_rows]),
        "INSIDE_COLLAPSE_N": sum(1 for r in eligible if r.get("inside_collapsed_to_bid")),
        "CONDITIONAL_FILLED_MARKOUT": {
            "60": cond_mid.get("MARKOUT60_MEAN"),
            "180": cond_mid.get("MARKOUT180_MEAN"),
            "300": cond_mid.get("MARKOUT300_MEAN"),
            "KIND": "FILL_TO_MID_SIGNAL_ANCHORED",
        },
        "UNCONDITIONAL_POLICY_MARKOUT": {
            "60": uncond_mid.get("MARKOUT60_MEAN"),
            "180": uncond_mid.get("MARKOUT180_MEAN"),
            "300": uncond_mid.get("MARKOUT300_MEAN"),
            "KIND": "FILL_TO_MID_SIGNAL_ANCHORED",
            "UNFILLED_AS_ZERO": True,
            "DENOMINATOR": len(eligible),
        },
        "CONDITIONAL_FILL_TO_BID": {
            "60": cond_bid.get("MARKOUT60_MEAN"),
            "180": cond_bid.get("MARKOUT180_MEAN"),
            "300": cond_bid.get("MARKOUT300_MEAN"),
            "KIND": "FILL_TO_BID_SIGNAL_ANCHORED",
        },
        "UNCONDITIONAL_FILL_TO_BID": {
            "60": uncond_bid.get("MARKOUT60_MEAN"),
            "180": uncond_bid.get("MARKOUT180_MEAN"),
            "300": uncond_bid.get("MARKOUT300_MEAN"),
            "KIND": "FILL_TO_BID_SIGNAL_ANCHORED",
            "UNFILLED_AS_ZERO": True,
            "DENOMINATOR": len(eligible),
        },
        "FILL_TIME_MARKOUT_DIAG": {
            "60": ft_arm.get("MARKOUT60_MEAN"),
            "180": ft_arm.get("MARKOUT180_MEAN"),
            "300": ft_arm.get("MARKOUT300_MEAN"),
            "KIND": "FILL_TO_MID_FILL_TIME_ANCHORED",
            "NOT_FOR_SELECTION": True,
        },
        "FILLED_GROSS_MID": gross.get("FILLED_GROSS_MID"),
        "UNFILLED_GROSS_MID": gross.get("UNFILLED_GROSS_MID"),
        "POSITIVE_DAY_N_180": uncond_mid.get("POSITIVE_DAY_N_180"),
        "NEGATIVE_DAY_N_180": uncond_mid.get("NEGATIVE_DAY_N_180"),
        "POSITIVE_DAY_N_300": uncond_mid.get("POSITIVE_DAY_N_300"),
        "NEGATIVE_DAY_N_300": uncond_mid.get("NEGATIVE_DAY_N_300"),
        "EX_BEST_180": uncond_mid.get("EX_BEST_180"),
        "EX_BEST_300": uncond_mid.get("EX_BEST_300"),
        "EX_TOP3_180": uncond_mid.get("EX_TOP3_180"),
        "EX_TOP3_300": uncond_mid.get("EX_TOP3_300"),
        "DROP_TOP_SYMBOL_180": uncond_mid.get("DROP_TOP_SYMBOL_180"),
        "DROP_TOP_SYMBOL_300": uncond_mid.get("DROP_TOP_SYMBOL_300"),
        "DROP_TOP3_180": drop_top3_mean(uncond_mid_rows, "markout_180"),
        "DROP_TOP3_300": drop_top3_mean(uncond_mid_rows, "markout_300"),
        "VS_E0": vs,
        "PAIRED_COND": paired,
        "EXTREME_ADVERSE_SELECTION": adverse,
        "COVERAGE_OK": coverage_ok,
        "EVIDENCE_LIMITED": evidence_limited,
        "MECHANISM_MEMBER": mechanism_member,
        "UNCOND_PRIMARY": slim_arm(uncond_mid),
        "COND_PRIMARY": slim_arm(cond_mid),
        "UNCOND_BID": slim_arm(uncond_bid),
        "COND_BID": slim_arm(cond_bid),
        "BID_POS_DAY_N_180": uncond_bid.get("POSITIVE_DAY_N_180"),
        "BID_NEG_DAY_N_180": uncond_bid.get("NEGATIVE_DAY_N_180"),
        "BID_POS_DAY_N_300": uncond_bid.get("POSITIVE_DAY_N_300"),
        "BID_NEG_DAY_N_300": uncond_bid.get("NEGATIVE_DAY_N_300"),
        "BID_EX_BEST_180": uncond_bid.get("EX_BEST_180"),
        "BID_EX_BEST_300": uncond_bid.get("EX_BEST_300"),
        "daily_uncond": uncond_mid.get("daily"),
        "daily_uncond_bid": uncond_bid.get("daily"),
    }
    return out


def family_mechanism(policies: dict[str, dict[str, Any]], family: str) -> dict[str, Any]:
    pairs = FAMILY_PAIRS[family]
    passing = []
    pair_ok = []
    isolated = []
    for a, b in pairs:
        pa = bool((policies.get(a) or {}).get("MECHANISM_MEMBER"))
        pb = bool((policies.get(b) or {}).get("MECHANISM_MEMBER"))
        rec = {"pair": [a, b], "A": pa, "B": pb, "BOTH": bool(pa and pb)}
        pair_ok.append(rec)
        if pa and pb:
            passing.extend([a, b])
    members = [aid for aid, fam, _w in POLICY_ARMS if fam == family]
    for aid in members:
        if not (policies.get(aid) or {}).get("MECHANISM_MEMBER"):
            continue
        adjacent_pass = any(aid in rec["pair"] and rec["BOTH"] for rec in pair_ok)
        if not adjacent_pass:
            isolated.append(aid)
    passing_ids = sorted(set(passing))
    return {
        "FAMILY": family,
        "SUPPORTED": bool(passing_ids),
        "PASSING_POLICIES": passing_ids,
        "ISOLATED_OPTIMUM": isolated,
        "PAIRS": pair_ok,
    }


def select_policy(passing_ids: list[str], policies: dict[str, dict[str, Any]]) -> Optional[str]:
    if not passing_ids:
        return None

    def key(aid: str) -> tuple:
        p = policies.get(aid) or {}
        wait = float(p.get("DELAY_BUDGET_SEC") or 99.0)
        fam = str(p.get("FAMILY") or "")
        agg = 0 if fam == "PASSIVE_BID" else 1
        fill = -int(p.get("FILLED_N") or 0)
        return (wait, agg, fill, aid)

    return sorted(passing_ids, key=key)[0]


def edge_repaired(policy: dict[str, Any] | None, *, coverage_ok: bool, integrity_ok: bool) -> dict[str, Any]:
    if not policy:
        return {
            "ENTRY_EXECUTION_EDGE_REPAIRED": False,
            "A_UNCOND_BID_180_GT0": False,
            "B_UNCOND_BID_300_GT0": False,
            "C_POS_DAYS_180": False,
            "D_POS_DAYS_300": False,
            "E_EX_BEST_180": False,
            "F_EX_BEST_300": False,
            "G_COVERAGE": False,
            "H_INTEGRITY": bool(integrity_ok),
        }
    bid = policy.get("UNCONDITIONAL_FILL_TO_BID") or {}
    a = _gt0(bid.get("180"))
    b = _gt0(bid.get("300"))
    c = int(policy.get("BID_POS_DAY_N_180") or 0) > int(policy.get("BID_NEG_DAY_N_180") or 0)
    d = int(policy.get("BID_POS_DAY_N_300") or 0) > int(policy.get("BID_NEG_DAY_N_300") or 0)
    e = _gt0(policy.get("BID_EX_BEST_180"))
    f = _gt0(policy.get("BID_EX_BEST_300"))
    g = bool(coverage_ok and policy.get("COVERAGE_OK"))
    h = bool(integrity_ok)
    ok = bool(a and b and c and d and e and f and g and h)
    return {
        "ENTRY_EXECUTION_EDGE_REPAIRED": ok,
        "A_UNCOND_BID_180_GT0": a,
        "B_UNCOND_BID_300_GT0": b,
        "C_POS_DAYS_180": c,
        "D_POS_DAYS_300": d,
        "E_EX_BEST_180": e,
        "F_EX_BEST_300": f,
        "G_COVERAGE": g,
        "H_INTEGRITY": h,
        "UNCOND_BID_60": bid.get("60"),
        "UNCOND_BID_180": bid.get("180"),
        "UNCOND_BID_300": bid.get("300"),
    }


def primary_deficiency(
    *,
    case: str,
    bid_fam: dict[str, Any],
    inside_fam: dict[str, Any],
    policies: dict[str, dict[str, Any]],
    repaired: dict[str, Any],
) -> str:
    if case == "F":
        return "INTEGRITY_FAILURE"
    if case == "C":
        return "PASSIVE_FILL_ADVERSE_SELECTION"
    if case == "D":
        return "INSIDE_SPREAD_EVIDENCE_LIMITED"
    if case == "A":
        return "NONE_ENTRY_EXECUTION_EDGE_REPAIRED"
    if case == "B":
        if not repaired.get("A_UNCOND_BID_180_GT0") or not repaired.get("B_UNCOND_BID_300_GT0"):
            return "EXECUTABLE_FILL_TO_BID_STILL_NONPOSITIVE"
        return "EXECUTABLE_EDGE_ROBUSTNESS_INCOMPLETE"
    adverse_n = sum(1 for p in policies.values() if p.get("EXTREME_ADVERSE_SELECTION"))
    cov_n = sum(1 for p in policies.values() if p.get("COVERAGE_OK"))
    mean_n = sum(1 for p in policies.values() if (p.get("VS_E0") or {}).get("MEAN_IMPROVE_180_300"))
    if cov_n == 0:
        return "FILL_COVERAGE_BELOW_20"
    if mean_n == 0:
        return "NO_UNCONDITIONAL_IMPROVEMENT_VS_E0"
    if adverse_n:
        return "PASSIVE_FILL_ADVERSE_SELECTION"
    if bid_fam.get("ISOLATED_OPTIMUM") or inside_fam.get("ISOLATED_OPTIMUM"):
        return "WAIT_BAND_ISOLATED_OPTIMUM"
    return "ENTRY_EXECUTION_NOT_ROBUST_VS_E0"


def decide_case(
    *,
    integrity_ok: bool,
    optimistic_n: int,
    bid_fam: dict[str, Any],
    inside_fam: dict[str, Any],
    policies: dict[str, dict[str, Any]],
    selected: Optional[str],
    repaired: dict[str, Any],
    mechanism: bool,
) -> dict[str, Any]:
    if (not integrity_ok) or int(optimistic_n) != 0:
        return {
            "CASE": "F",
            "VERDICT": "SIMPLE_TECH_V12_INTEGRITY_FAILED",
            "NEXT": "NON_INTERFERENCE_FAIL. Do not adopt execution. Do not start EXIT.",
        }
    inside_thin = all(not (policies.get(aid) or {}).get("COVERAGE_OK") for aid, fam, _w in POLICY_ARMS if fam == "IMPROVE_1TICK")
    inside_looks = False
    for aid, fam, _w in POLICY_ARMS:
        if fam != "IMPROVE_1TICK":
            continue
        p = policies.get(aid) or {}
        cond = p.get("CONDITIONAL_FILLED_MARKOUT") or {}
        vs = p.get("VS_E0") or {}
        if (p.get("FILLED_N") or 0) > 0 and (
            bool(vs.get("MEAN_IMPROVE_180_300"))
            or (_gt0(cond.get("180")) and _gt0(cond.get("300")))
        ):
            inside_looks = True
            break
    bid_supported = bool(bid_fam.get("SUPPORTED"))
    inside_supported = bool(inside_fam.get("SUPPORTED"))
    adverse_cov = [
        aid
        for aid, p in policies.items()
        if p.get("EXTREME_ADVERSE_SELECTION") and p.get("COVERAGE_OK")
    ]
    if mechanism and repaired.get("ENTRY_EXECUTION_EDGE_REPAIRED") and selected:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V12_ENTRY_EXECUTION_EDGE_REPAIRED",
            "NEXT": (
                "Keep frozen B1 stack plus selected ENTRY execution as a development freeze candidate. "
                "Run structure verification before any EXIT family. TRUE_OOS=false. Not a Runtime candidate."
            ),
        }
    if mechanism and selected:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V12_EXECUTION_HELPFUL_EDGE_STILL_INSUFFICIENT",
            "NEXT": (
                "Do not retune the frozen B1 signal. ENTRY execution helps vs E0 but does not make "
                "signal-anchored FILL->BID unconditional 180/300 robustly positive. Do not start EXIT."
            ),
        }
    if (not mechanism) and adverse_cov:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V12_PASSIVE_FILL_ADVERSE_SELECTION",
            "NEXT": (
                "Do not adopt passive ENTRY execution. Cheaper fills miss better Gross-Mid signals. "
                "Do not retune the signal. Do not start EXIT."
            ),
        }
    if (not bid_supported) and inside_thin and inside_looks:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V12_INSIDE_SPREAD_EVIDENCE_LIMITED",
            "NEXT": (
                "Do not adopt inside-spread execution. Ask-cross evidence cannot support a counterfactual queue. "
                "Do not invent touch-fills. Do not start EXIT."
            ),
        }
    if inside_supported and (not bid_supported) and all((policies.get(aid) or {}).get("EVIDENCE_LIMITED") for aid, fam, _w in POLICY_ARMS if fam == "IMPROVE_1TICK"):
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V12_INSIDE_SPREAD_EVIDENCE_LIMITED",
            "NEXT": "Do not adopt inside-spread execution. Evidence is coverage-limited. Do not start EXIT.",
        }
    return {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_V12_ENTRY_EXECUTION_NOT_SUPPORTED",
        "NEXT": (
            "Do not adopt passive or inside ENTRY execution on this frozen B1 stack. "
            "Do not retune EMA/BB/RCI. Do not add spread/TOD/Board gates. Do not start EXIT."
        ),
    }

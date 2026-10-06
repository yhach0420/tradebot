"""V11 markout families, exact quote cost decomp, robustness, diagnostic TOD/spread. No gates."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v8_analyze import DAYS, arm_metrics, core_edge_gate
from research.simple_tech_entry_family.v9_analyze import tod_pack
from research.simple_tech_entry_family.v11_spec import ROLE_MIN_EXECUTABLE_N

KIND_PREFIX = {"GROSS_MID": "gross", "ENTRY_CROSS": "cross", "FULL_EXECUTABLE": "full"}


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _lt0(v: Any) -> bool:
    return v is not None and float(v) < -1e-12


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def remap_kind(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    pref = KIND_PREFIX[kind]
    out = []
    for r in rows:
        rec = dict(r)
        for h in (60, 180, 300):
            rec[f"markout_{h}"] = r.get(f"{pref}_{h}")
        out.append(rec)
    return out


def family_metrics(rows: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    mapped = remap_kind(rows, kind)
    funnel = {
        "ARM_ID": kind,
        "SIGNAL_N": len(rows),
        "EXECUTABLE_SIGNAL_N": sum(1 for r in rows if r.get("executable_signal")),
        "rows": mapped,
        "exe_rows": [r for r in mapped if r.get("executable_signal")],
    }
    arm = arm_metrics(funnel, list(DAYS))
    arm["KIND"] = kind
    return arm


def robust_edge(arm: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    pack = {
        "MARKOUT60_MEAN": arm.get("MARKOUT60_MEAN"),
        "MARKOUT180_MEAN": arm.get("MARKOUT180_MEAN"),
        "MARKOUT300_MEAN": arm.get("MARKOUT300_MEAN"),
        "MARKOUT180_MEDIAN": arm.get("MARKOUT180_MEDIAN"),
        "MARKOUT300_MEDIAN": arm.get("MARKOUT300_MEDIAN"),
        "POSITIVE_DAY_N_180": arm.get("POSITIVE_DAY_N_180"),
        "NEGATIVE_DAY_N_180": arm.get("NEGATIVE_DAY_N_180"),
        "POSITIVE_DAY_N_300": arm.get("POSITIVE_DAY_N_300"),
        "NEGATIVE_DAY_N_300": arm.get("NEGATIVE_DAY_N_300"),
        "EX_BEST_180": arm.get("EX_BEST_180"),
        "EX_BEST_300": arm.get("EX_BEST_300"),
        "EXECUTABLE_SIGNAL_N": arm.get("EXECUTABLE_SIGNAL_N"),
    }
    return core_edge_gate(pack, integrity_ok=integrity_ok)


def all_means_positive(arm: dict[str, Any]) -> bool:
    return bool(_gt0(arm.get("MARKOUT60_MEAN")) and _gt0(arm.get("MARKOUT180_MEAN")) and _gt0(arm.get("MARKOUT300_MEAN")))


def means_180_300_positive(arm: dict[str, Any]) -> bool:
    return bool(_gt0(arm.get("MARKOUT180_MEAN")) and _gt0(arm.get("MARKOUT300_MEAN")))


def means_180_300_negative(arm: dict[str, Any]) -> bool:
    return bool(_lt0(arm.get("MARKOUT180_MEAN")) and _lt0(arm.get("MARKOUT300_MEAN")))


def all_means_negative(arm: dict[str, Any]) -> bool:
    return bool(_lt0(arm.get("MARKOUT60_MEAN")) and _lt0(arm.get("MARKOUT180_MEAN")) and _lt0(arm.get("MARKOUT300_MEAN")))


def robust_negative(arm: dict[str, Any]) -> bool:
    med_neg = _lt0(arm.get("MARKOUT180_MEDIAN")) and _lt0(arm.get("MARKOUT300_MEDIAN"))
    days_neg = int(arm.get("NEGATIVE_DAY_N_180") or 0) >= int(arm.get("POSITIVE_DAY_N_180") or 0) and int(
        arm.get("NEGATIVE_DAY_N_300") or 0
    ) >= int(arm.get("POSITIVE_DAY_N_300") or 0)
    return bool(all_means_negative(arm) and med_neg and days_neg)


def decomp_horizon(rows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    hid = int(h)
    return {
        "HORIZON_SEC": hid,
        "GROSS_DIRECTIONAL_EDGE": _mean([r.get(f"gross_{hid}") for r in rows]),
        "ENTRY_HALF_SPREAD_BURDEN": _mean([r.get(f"entry_burden_{hid}") for r in rows]),
        "FUTURE_EXIT_HALF_SPREAD_BURDEN": _mean([r.get(f"exit_burden_{hid}") for r in rows]),
        "TOTAL_EXECUTABLE_DEGRADATION": _mean([r.get(f"total_deg_{hid}") for r in rows]),
        "GROSS_MEDIAN": _median([r.get(f"gross_{hid}") for r in rows]),
        "ENTRY_BURDEN_MEDIAN": _median([r.get(f"entry_burden_{hid}") for r in rows]),
        "EXIT_BURDEN_MEDIAN": _median([r.get(f"exit_burden_{hid}") for r in rows]),
        "TOTAL_DEG_MEDIAN": _median([r.get(f"total_deg_{hid}") for r in rows]),
        "N": sum(1 for r in rows if _finite(r.get(f"gross_{hid}")) and _finite(r.get(f"full_{hid}"))),
        "from_actual_quotes": True,
        "not_an_approximation": True,
    }


def spread_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [float(r["spread0_bps"]) for r in rows if _finite(r.get("spread0_bps"))]
    if not xs:
        return {"N": 0, "diagnostic_only": True, "not_a_gate": True}
    arr = np.asarray(xs, dtype=float)
    q25, q50, q75 = (float(x) for x in np.quantile(arr, [0.25, 0.5, 0.75]))
    return {
        "N": int(arr.size),
        "MEAN": float(np.mean(arr)),
        "MEDIAN": q50,
        "Q25": q25,
        "Q75": q75,
        "MIN": float(np.min(arr)),
        "MAX": float(np.max(arr)),
        "diagnostic_only": True,
        "not_a_gate": True,
    }


def spread_quartile_means(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    xs = [r for r in rows if _finite(r.get("spread0_bps"))]
    if len(xs) < 4:
        return [{"diagnostic_only": True, "not_a_gate": True, "N": len(xs)}]
    qs = [float(x) for x in np.quantile([float(r["spread0_bps"]) for r in xs], [0.25, 0.5, 0.75])]
    bands = [
        ("Q1_LE_Q25", lambda v: v <= qs[0] + 1e-12),
        ("Q2_Q25_Q50", lambda v: qs[0] < v <= qs[1] + 1e-12),
        ("Q3_Q50_Q75", lambda v: qs[1] < v <= qs[2] + 1e-12),
        ("Q4_GT_Q75", lambda v: v > qs[2] + 1e-12),
    ]
    out = []
    for lab, fn in bands:
        hit = [r for r in xs if fn(float(r["spread0_bps"]))]
        out.append(
            {
                "band": lab,
                "N": len(hit),
                "SPREAD_MEAN": _mean([r.get("spread0_bps") for r in hit]),
                "GROSS180_MEAN": _mean([r.get("gross_180") for r in hit]),
                "CROSS180_MEAN": _mean([r.get("cross_180") for r in hit]),
                "FULL180_MEAN": _mean([r.get("full_180") for r in hit]),
                "GROSS300_MEAN": _mean([r.get("gross_300") for r in hit]),
                "FULL300_MEAN": _mean([r.get("full_300") for r in hit]),
                "diagnostic_only": True,
                "not_a_gate": True,
            }
        )
    return out


def tod_kind(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    mapped = remap_kind(rows, kind)
    pack = tod_pack(mapped)
    for rec in pack:
        rec["kind"] = kind
        rec["diagnostic_only"] = True
        rec["not_a_gate"] = True
    return pack


def q5_not_only_one_state(tod: list[dict[str, Any]], spread_bands: list[dict[str, Any]]) -> dict[str, Any]:
    signs = []
    for rec in tod:
        m = rec.get("MARKOUT180_MEAN")
        if m is None:
            continue
        signs.append(1 if float(m) > 1e-12 else (-1 if float(m) < -1e-12 else 0))
    mixed_tod = bool(1 in signs and -1 in signs)
    sp_signs = []
    for rec in spread_bands:
        m = rec.get("GROSS180_MEAN")
        if m is None:
            continue
        sp_signs.append(1 if float(m) > 1e-12 else (-1 if float(m) < -1e-12 else 0))
    mixed_spread = bool(1 in sp_signs and -1 in sp_signs)
    return {
        "Q5_RESIDUAL_NOT_ONLY_ONE_TOD_OR_SPREAD": bool(mixed_tod or mixed_spread),
        "MIXED_TOD_SIGN": mixed_tod,
        "MIXED_SPREAD_QUARTILE_SIGN": mixed_spread,
        "diagnostic_only": True,
        "not_a_gate": True,
    }


def decide_case(
    *,
    mid: dict[str, Any],
    cross: dict[str, Any],
    full: dict[str, Any],
    mid_robust: dict[str, Any],
    integrity_ok: bool,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": "SIMPLE_TECH_V11_INTEGRITY_FAILED",
            "PRIMARY_INTERPRETATION": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }
    mid_ok = bool(mid_robust.get("CORE_ENTRY_EDGE_SUPPORTED"))
    mid_pos = all_means_positive(mid)
    mid_neg = robust_negative(mid)
    cross_neg = means_180_300_negative(cross)
    full_neg = means_180_300_negative(full) or all_means_negative(full)
    n = int(mid.get("EXECUTABLE_SIGNAL_N") or 0)
    thin = n < int(ROLE_MIN_EXECUTABLE_N)

    if thin:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V11_SIGNAL_EXECUTION_ATTRIBUTION_MIXED",
            "PRIMARY_INTERPRETATION": "B1 executable coverage is too thin to attribute residual negative edge.",
            "NEXT": "Do not change the frozen B1 stack. Do not add Board/PA/Volume/Persistence. No EXIT.",
        }
    if mid_neg:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V11_SIGNAL_EDGE_STILL_INSUFFICIENT",
            "PRIMARY_INTERPRETATION": (
                "Mid->Mid is still robustly negative. Residual B1 loss is underlying direction, not executable cost. "
                "Do not flee to execution architecture."
            ),
            "NEXT": "Return to remaining signal-mechanism RCA on the frozen T3+Pullback+RCI stack. No EXIT. No inverse Board gate.",
        }
    if mid_ok and cross_neg:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V11_ENTRY_CROSS_COST_DOMINANT",
            "PRIMARY_INTERPRETATION": (
                "Gross Mid->Mid is robustly positive, but Ask->Mid is already negative. "
                "ENTRY crossing / entry price is the primary deficiency."
            ),
            "NEXT": (
                "Keep the frozen B1 signal stack. Next run: ENTRY price/execution architecture "
                "(passive Bid, inside-spread limit, wait budget) as a separate precommit. No EXIT."
            ),
        }
    if mid_ok and full_neg:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V11_GROSS_SIGNAL_EDGE_EXECUTION_COST_DOMINANT",
            "PRIMARY_INTERPRETATION": (
                "Gross Mid->Mid is robustly positive while Ask->Bid stays negative. "
                "Signal selection is a gross-edge candidate; executable round-trip cost is the primary deficiency."
            ),
            "NEXT": (
                "Keep the frozen B1 signal stack. Next run: ENTRY execution architecture "
                "(passive Bid, inside-spread limit, wait budget) as a separate precommit. No EXIT."
            ),
        }
    if mid_pos and cross_neg:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V11_ENTRY_CROSS_COST_DOMINANT",
            "PRIMARY_INTERPRETATION": (
                "Mid->Mid means are positive at 60/180/300 but Ask->Mid is already negative. "
                "ENTRY cross cost dominates; gross robustness is incomplete."
            ),
            "NEXT": (
                "Keep the frozen B1 signal stack. Next: ENTRY price/execution research. No EXIT."
            ),
        }
    if mid_pos and full_neg:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V11_SIGNAL_EXECUTION_ATTRIBUTION_MIXED",
            "PRIMARY_INTERPRETATION": (
                "Mid->Mid means are positive but not robust, while Ask->Bid is negative. Attribution is fragile."
            ),
            "NEXT": "Do not adopt execution architecture yet. Re-read Mid robustness before changing ENTRY. No EXIT.",
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V11_SIGNAL_EXECUTION_ATTRIBUTION_MIXED",
        "PRIMARY_INTERPRETATION": "Gross vs executable signs are mixed or fragile across 60/180/300.",
        "NEXT": "Do not change the frozen B1 stack. Do not add Board/PA/Volume. No EXIT.",
    }

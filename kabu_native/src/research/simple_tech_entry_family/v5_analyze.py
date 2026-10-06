"""V5 Reversal quality RCA: Spearman, quartiles, day stability, cross audit, mechanism gates. No thresholds. No C14."""
from __future__ import annotations

from typing import Any, Optional

from research.simple_tech_entry_family.v4_analyze import (
    _mean,
    _median,
    _pos,
    axis_pack,
    mechanism_gates as _vq_mechanism_gates,
)
from research.simple_tech_entry_family.v5_spec import MECHANISM_PICK_ORDER

AXES = (
    ("RQ1", "RCI_LEVEL"),
    ("RQ2", "RCI_DELTA"),
    ("RQ3", "MULTI_BAR_SLOPE"),
    ("RQ4", "RECOVERY_FRACTION"),
)


def mechanism_gates(pack: dict[str, Any], good6: list[dict[str, Any]], other23: list[dict[str, Any]], feat: str) -> dict[str, Any]:
    g = _vq_mechanism_gates(pack, good6, other23, feat)
    g["H_PRE_REVERSAL"] = g.pop("H_PRE_VOLUME")
    return g


def good_vs_other(g6: list[dict[str, Any]], o23: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"GOOD6_N": len(g6), "OTHER23_N": len(o23)}
    align = None
    align_key = None
    for feat, name in AXES:
        gm = _median([r.get(feat) for r in g6])
        om = _median([r.get(feat) for r in o23])
        out[f"GOOD6_{feat}"] = gm
        out[f"OTHER23_{feat}"] = om
        out[f"GOOD6_GT_{feat}"] = bool(gm is not None and om is not None and float(gm) > float(om))
        if out[f"GOOD6_GT_{feat}"] and align is None:
            align = name
            align_key = feat
    out["align_rq"] = align
    out["align_key"] = align_key
    out["align_rq_role"] = "GOOD6_VS_OTHER23_FIRST_HIGHER_AXIS"
    out["align_rq_is_supported_mechanism"] = False
    out["good6_higher_axes"] = [name for feat, name in AXES if out.get(f"GOOD6_GT_{feat}")]
    return out


def cross_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pas = [r for r in rows if r.get("rci_cross")]
    fail = [r for r in rows if not r.get("rci_cross")]

    def _pack(xs: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_N": len(xs),
            f"{prefix}_MARKOUT_60_MEAN": _mean([r.get("markout_60") for r in xs]),
            f"{prefix}_MARKOUT_180_MEAN": _mean([r.get("markout_180") for r in xs]),
            f"{prefix}_MARKOUT_300_MEAN": _mean([r.get("markout_300") for r in xs]),
            f"{prefix}_MARKOUT_180_MEDIAN": _median([r.get("markout_180") for r in xs]),
            f"{prefix}_MARKOUT_300_MEDIAN": _median([r.get("markout_300") for r in xs]),
            f"{prefix}_MFE_MEDIAN": _median([r.get("mfe_bps") for r in xs]),
            f"{prefix}_MAE_MEDIAN": _median([r.get("mae_bps") for r in xs]),
        }

    out = {**_pack(pas, "PASS"), **_pack(fail, "FAIL")}

    def _gt(a: Any, b: Any) -> Optional[bool]:
        if a is None or b is None:
            return None
        return bool(float(a) > float(b))

    out["PASS_GT_FAIL_60"] = _gt(out.get("PASS_MARKOUT_60_MEAN"), out.get("FAIL_MARKOUT_60_MEAN"))
    out["PASS_GT_FAIL_180"] = _gt(out.get("PASS_MARKOUT_180_MEAN"), out.get("FAIL_MARKOUT_180_MEAN"))
    out["PASS_GT_FAIL_300"] = _gt(out.get("PASS_MARKOUT_300_MEAN"), out.get("FAIL_MARKOUT_300_MEAN"))
    out["CROSS_HELPS_PRIMARY"] = bool(out.get("PASS_GT_FAIL_180") and out.get("PASS_GT_FAIL_300"))
    return out


def taxonomy_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "R1_WEAK_CROSS",
        "R2_STRONG_RCI_RECOVERY",
        "R3_FAST_RCI_REVERSAL",
        "R4_SINGLE_BAR_SPIKE",
        "R5_MULTI_BAR_RCI_RECOVERY",
        "R6_RCI_RECOVERED_BUT_PRICE_NOT_FOLLOWING",
    )
    out = []
    for k in keys:
        hit = [r for r in rows if k in (r.get("taxonomy") or [])]
        out.append(
            {
                "label": k,
                "N": len(hit),
                "MARKOUT180_MEAN": _mean([r.get("markout_180") for r in hit]),
                "MARKOUT300_MEAN": _mean([r.get("markout_300") for r in hit]),
            }
        )
    return out


def pick_mechanism(gates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    names = {k: n for k, n in AXES}
    hits = [k for k in MECHANISM_PICK_ORDER if (gates.get(k) or {}).get("SUPPORTED")]
    if not hits:
        return {
            "SUPPORTED_REVERSAL_MECHANISM": "NONE",
            "VERDICT": "SIMPLE_TECH_REVERSAL_NO_ROBUST_MECHANISM_FOUND",
            "PRIMARY_DEFICIENCY": "REVERSAL_QUALITY_UNRESOLVED",
            "NEXT": (
                "No robust reversal-quality mechanism. Family stays open. "
                "Re-compare remaining Trend/Pullback evidence with V3/V4. "
                "Do not change RCI. Do not add Persistence. No EXIT."
            ),
        }
    k = hits[0]
    defn = {
        "RQ1": "RCI_CROSS_IGNORES_LEVEL",
        "RQ2": "RCI_CROSS_IGNORES_DELTA",
        "RQ3": "RCI_CROSS_IGNORES_MULTI_BAR_SLOPE",
        "RQ4": "RCI_CROSS_IGNORES_RECOVERY_FRACTION",
    }[k]
    return {
        "SUPPORTED_REVERSAL_MECHANISM": names[k],
        "VERDICT": "SIMPLE_TECH_REVERSAL_QUALITY_MECHANISM_SUPPORTED",
        "PRIMARY_DEFICIENCY": defn,
        "NEXT": "V5_REVERSAL_RULE_PRECOMMIT. Do not choose a threshold in this run. Do not add Persistence. MA/BB/Volume/Price Action/Board stay frozen. No EXIT.",
        "SUPPORTED_AXIS": k,
    }


def answers(
    p1: dict[str, Any],
    p2: dict[str, Any],
    p3: dict[str, Any],
    p4: dict[str, Any],
    g6: dict[str, Any],
    gates: dict[str, dict[str, Any]],
    cross: dict[str, Any],
    supported: str,
) -> dict[str, Any]:
    q1 = "yes_cross_helps_markout" if cross.get("CROSS_HELPS_PRIMARY") else "no_cross_does_not_improve_primary_markouts"
    q2 = "yes_level_related" if _pos(p1.get("SPEARMAN_180")) and _pos(p1.get("SPEARMAN_300")) else "no_robust_level_relation"
    q3 = "yes_delta_related" if _pos(p2.get("SPEARMAN_180")) and _pos(p2.get("SPEARMAN_300")) else "no_robust_delta_relation"
    q4 = "unavailable_or_weaker"
    if p3.get("SPEARMAN_180") is not None and p2.get("SPEARMAN_180") is not None:
        d3 = p3.get("DAY_180") or {}
        d2 = p2.get("DAY_180") or {}
        more_stable = int(d3.get("POSITIVE_RELATION_DAY_N") or 0) > int(d2.get("POSITIVE_RELATION_DAY_N") or 0)
        q4 = "multi_bar_more_stable_than_delta" if more_stable and _pos(p3.get("SPEARMAN_180")) else "multi_bar_not_more_stable_than_delta"
    q5 = "no_shared_state"
    if g6.get("align_rq"):
        q5 = f"higher_{g6['align_rq']}_than_other23"
    q6 = "no_supported_mechanism_pre_reversal_multi_day"
    axis = None
    for k, _n in AXES:
        if (gates.get(k) or {}).get("SUPPORTED"):
            axis = k
            break
    if axis:
        gg = gates.get(axis) or {}
        if bool(gg.get("D_MULTI_DAY")) and bool(gg.get("H_PRE_REVERSAL")):
            q6 = f"{axis} sign holds on full PRE_REVERSAL population across multiple days"
    return {
        "Q1": q1,
        "Q2": q2,
        "Q3": q3,
        "Q4": q4,
        "Q5": q5,
        "Q6": q6,
        "Q6_AXIS": axis or "NONE",
        "Q6_USES_ALIGN_RQ": False,
        "Q5_USES_ALIGN_RQ": True,
        "ALIGN_RQ_ROLE": "GOOD6_VS_OTHER23_FIRST_HIGHER_AXIS",
        "SUPPORTED_REVERSAL_MECHANISM": supported,
    }

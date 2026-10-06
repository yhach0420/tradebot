"""Matched vs unmatched treatments on pre-treatment covariates only. Not an ENTRY filter."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.support_resistance_matched_separation_not_a_strategy_v1 import P20_MATCH_GAP_MATERIAL, SMD_MATERIAL


PRE_VARS = (
    "tod_min",
    "as_resistance",
    "dist_open_atr",
    "r1",
    "r3",
    "r5",
    "rng_rel",
    "vol_rel",
    "gap_num",
    "mkt_num",
    "sec_num",
    "zone_age",
    "touch_count",
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _arr(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    return np.asarray(xs, dtype=float)


def smd(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 5 or b.size < 5:
        return None
    va, vb = float(np.var(a)), float(np.var(b))
    den = np.sqrt(0.5 * (va + vb))
    if den <= 1e-12:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / den)


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([1.0 if x else 0.0 for x in xs]))


def _med(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.median(xs))


def matchability_audit(rows: list[dict[str, Any]], *, question: str) -> dict[str, Any]:
    eval_rows = [r for r in rows if str(r.get("block") or "") in {"D2", "D3", "D4"}]
    matched = [r for r in eval_rows if r.get("matched")]
    unmatched = [r for r in eval_rows if not r.get("matched")]
    diffs = {}
    material_cov = []
    for k in PRE_VARS:
        d = smd(_arr(matched, k), _arr(unmatched, k))
        diffs[k] = d
        if d is not None and abs(d) >= SMD_MATERIAL:
            material_cov.append(k)
    p20_m = _rate(matched, "tr_p20_before_m20")
    p20_u = _rate(unmatched, "tr_p20_before_m20")
    p20_gap = (float(p20_m) - float(p20_u)) if p20_m is not None and p20_u is not None else None
    outcome_material = bool(p20_gap is not None and abs(p20_gap) >= P20_MATCH_GAP_MATERIAL)
    material = bool(material_cov or outcome_material)
    kind = "unknown"
    if matched:
        res_rate = float(np.mean([1.0 if r.get("as_resistance") else 0.0 for r in matched]))
        u_res = float(np.mean([1.0 if r.get("as_resistance") else 0.0 for r in unmatched])) if unmatched else None
        tod_m = _med(matched, "tod_min")
        tod_u = _med(unmatched, "tod_min")
        kind = (
            f"matched_resistance_share={round(res_rate, 3)}; "
            f"unmatched_resistance_share={None if u_res is None else round(u_res, 3)}; "
            f"matched_tod_min={tod_m}; unmatched_tod_min={tod_u}"
        )
    return {
        "question": question,
        "used_as_entry_filter": False,
        "treatment_all_n": len(eval_rows),
        "matched_n": len(matched),
        "unmatched_n": len(unmatched),
        "match_rate": (len(matched) / len(eval_rows)) if eval_rows else None,
        "standardized_differences": diffs,
        "material_covariates": material_cov,
        "matched_p20": p20_m,
        "unmatched_p20": p20_u,
        "p20_gap_matched_minus_unmatched": p20_gap,
        "matched_p40": _rate(matched, "tr_p40_before_m20"),
        "unmatched_p40": _rate(unmatched, "tr_p40_before_m20"),
        "matched_p80": _rate(matched, "tr_p80_before_m30"),
        "unmatched_p80": _rate(unmatched, "tr_p80_before_m30"),
        "matched_mfe": _med(matched, "tr_mfe_bps"),
        "unmatched_mfe": _med(unmatched, "tr_mfe_bps"),
        "matched_mae": _med(matched, "tr_mae_bps"),
        "unmatched_mae": _med(unmatched, "tr_mae_bps"),
        "matched_end": _med(matched, "tr_end_bps"),
        "unmatched_end": _med(unmatched, "tr_end_bps"),
        "MATCHABILITY_SELECTION_IS_MATERIAL": material,
        "what_is_matchable": kind,
    }

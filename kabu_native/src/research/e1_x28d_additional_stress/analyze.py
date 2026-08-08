"""Stress evaluation metrics, views, LOSO/LODO, stop diagnostics, program decision."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from . import (
    FOCUS_SYMBOLS,
    NEAR_STOP_FRAC,
    REASON_MAP,
    STRESS_DAYS,
    VERDICT_FAILURE,
    VERDICT_MIXED,
    VERDICT_SUPPORT,
)


def _pf(pnls: np.ndarray) -> Optional[float]:
    gains = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    g = float(np.sum(gains)) if gains.size else 0.0
    l = float(np.abs(np.sum(losses))) if losses.size else 0.0
    if l == 0:
        return None
    return g / l


def summarize_all(
    *,
    mat: dict[str, np.ndarray],
    mask: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    population: str = "SELECTED",
) -> dict[str, Any]:
    base = mask if population == "SELECTED" else ~mask
    valid = base & mat["valid"]
    idx = np.where(valid)[0]
    elig = int(base.sum())
    n = int(idx.size)
    if n == 0:
        return {
            "trades": 0, "eligible": elig, "coverage": (0.0 if elig else None),
            "days": 0, "symbols": 0, "avg_return_bps": None, "median_return_bps": None,
            "avg_pnl": None, "total_pnl": None, "profit_factor": None,
            "exit_reason_counts": {}, "win_rate": None,
        }
    rets = mat["ret_bps"][idx]
    pnls = mat["pnl"][idx]
    d = dates[idx]
    s = symbols[idx]
    reasons_raw = Counter(mat["reason"][idx].tolist())
    reasons = {REASON_MAP.get(k, str(k).upper()): int(v) for k, v in reasons_raw.items()}
    return {
        "trades": n,
        "eligible": elig,
        "coverage": n / elig if elig else None,
        "days": int(np.unique(d).size),
        "symbols": int(np.unique(s).size),
        "avg_return_bps": float(np.mean(rets)),
        "median_return_bps": float(np.median(rets)),
        "avg_pnl": float(np.mean(pnls)),
        "total_pnl": float(np.sum(pnls)),
        "profit_factor": _pf(pnls),
        "win_rate": float(np.mean(pnls > 0)),
        "exit_reason_counts": reasons,
    }


def entry_delta(mat, sel_mask, dates, symbols) -> tuple[Optional[float], dict, dict]:
    sel = summarize_all(mat=mat, mask=sel_mask, dates=dates, symbols=symbols, population="SELECTED")
    comp = summarize_all(mat=mat, mask=sel_mask, dates=dates, symbols=symbols, population="COMPLEMENT")
    a, b = sel.get("avg_return_bps"), comp.get("avg_return_bps")
    delta = (float(a - b) if a is not None and b is not None else None)
    return delta, sel, comp


def personalization_delta(
    mat_a: dict[str, np.ndarray],
    mat_b: dict[str, np.ndarray],
    sel_mask: np.ndarray,
) -> dict[str, Any]:
    common = sel_mask & mat_a["valid"] & mat_b["valid"]
    idx = np.where(common)[0]
    if idx.size == 0:
        return {"n": 0, "delta_avg_return": None, "delta_avg_pnl": None}
    ra, rb = mat_a["ret_bps"][idx], mat_b["ret_bps"][idx]
    pa, pb = mat_a["pnl"][idx], mat_b["pnl"][idx]
    return {
        "n": int(idx.size),
        "delta_avg_return": float(np.mean(ra) - np.mean(rb)),
        "delta_avg_pnl": float(np.mean(pa) - np.mean(pb)),
        "a_avg_return": float(np.mean(ra)),
        "b_avg_return": float(np.mean(rb)),
    }


def direction_vs_x28c(stress_ret: Optional[float], x28c_ret: Optional[float]) -> str:
    if stress_ret is None or x28c_ret is None:
        return "X28C_TO_X28D_INSUFFICIENT"
    if (stress_ret > 0) == (x28c_ret > 0):
        return "X28C_TO_X28D_SAME_DIRECTION"
    return "X28C_TO_X28D_REVERSED"


def stop_diagnostics(
    *,
    mat: dict[str, np.ndarray],
    sel_mask: np.ndarray,
    stop_bps: Optional[float],
) -> dict[str, Any]:
    idx = np.where(sel_mask & mat["valid"])[0]
    n = int(idx.size)
    if n == 0 or stop_bps is None:
        return {
            "trades": n, "hard_stop_exit_n": 0, "hard_stop_exit_rate": None,
            "gross_loss_from_hard_stops": None,
            "avg_mae": None, "median_mae": None,
            "mae_q25": None, "mae_q50": None, "mae_q75": None,
            "avg_mfe": None, "winner_mae": None, "loser_mae": None,
            "near_stop_count": 0, "near_stop_recovery_count": 0,
            "near_stop_recovery_rate": None,
            "exit_reason_counts": {},
        }
    reasons = Counter(mat["reason"][idx].tolist())
    mapped = {REASON_MAP.get(k, str(k).upper()): int(v) for k, v in reasons.items()}
    hard_n = int(reasons.get("hard_stop", 0))
    hard_idx = idx[mat["reason"][idx] == "hard_stop"]
    hard_loss = float(np.sum(mat["pnl"][hard_idx][mat["pnl"][hard_idx] < 0])) if hard_idx.size else 0.0
    mae = mat["mae_bps"][idx]
    mfe = mat["mfe_bps"][idx]
    rets = mat["ret_bps"][idx]
    win = rets > 0
    lose = rets <= 0
    near = mae <= (-NEAR_STOP_FRAC * float(stop_bps))
    near_rec = near & (rets > 0)
    return {
        "trades": n,
        "hard_stop_exit_n": hard_n,
        "hard_stop_exit_rate": hard_n / n,
        "gross_loss_from_hard_stops": hard_loss,
        "avg_mae": float(np.nanmean(mae)),
        "median_mae": float(np.nanmedian(mae)),
        "mae_q25": float(np.nanquantile(mae, 0.25)),
        "mae_q50": float(np.nanquantile(mae, 0.50)),
        "mae_q75": float(np.nanquantile(mae, 0.75)),
        "avg_mfe": float(np.nanmean(mfe)),
        "winner_mae": float(np.nanmean(mae[win])) if win.any() else None,
        "loser_mae": float(np.nanmean(mae[lose])) if lose.any() else None,
        "near_stop_count": int(near.sum()),
        "near_stop_recovery_count": int(near_rec.sum()),
        "near_stop_recovery_rate": (float(near_rec.sum() / near.sum()) if near.any() else None),
        "exit_reason_counts": mapped,
        "configured_stop_bps": float(stop_bps),
        "note": "hard_stop_exit_n is actual EXIT reason count, not configured STOP class count",
    }


def lodo(
    *,
    mat: dict[str, np.ndarray],
    sel_mask: np.ndarray,
    dates: np.ndarray,
    metric: str = "ret",
) -> dict[str, Any]:
    full_idx = np.where(sel_mask & mat["valid"])[0]
    if full_idx.size == 0:
        return {
            "max_day_contribution_share": None,
            "positive_LODO_count": 0, "negative_LODO_count": 0,
            "without": {},
        }
    full_vals = mat["ret_bps"][full_idx] if metric == "ret" else mat["pnl"][full_idx]
    full_mean = float(np.mean(full_vals))
    day_contrib = {}
    without = {}
    pos = neg = 0
    for day in STRESS_DAYS:
        keep = full_idx[dates[full_idx] != day]
        day_only = full_idx[dates[full_idx] == day]
        share = None
        if full_vals.size and np.abs(full_mean) > 1e-12 and day_only.size:
            day_mean = float(np.mean(mat["ret_bps"][day_only]))
            # contribution share ≈ weight * day_mean / full_mean
            w = day_only.size / full_idx.size
            share = float(w * day_mean / full_mean) if abs(full_mean) > 1e-12 else None
        day_contrib[day] = share
        if keep.size == 0:
            without[f"without_{day}"] = None
            continue
        m = float(np.mean(mat["ret_bps"][keep]))
        without[f"without_{day}"] = m
        if m > 0:
            pos += 1
        elif m < 0:
            neg += 1
    shares = [abs(v) for v in day_contrib.values() if v is not None]
    return {
        "max_day_contribution_share": max(shares) if shares else None,
        "positive_LODO_count": pos,
        "negative_LODO_count": neg,
        "without": without,
        "day_contribution_share": day_contrib,
        "note": "3 days only — not labeled robust",
    }


def loso(
    *,
    mat: dict[str, np.ndarray],
    sel_mask: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    full_idx = np.where(sel_mask & mat["valid"])[0]
    if full_idx.size == 0:
        return {
            "max_symbol_contribution_share": None,
            "positive_LOSO_count": 0, "negative_LOSO_count": 0,
            "focus": {s: "NOT_PRESENT" for s in FOCUS_SYMBOLS},
        }
    full_mean = float(np.mean(mat["ret_bps"][full_idx]))
    uniq = sorted(set(symbols[full_idx].tolist()))
    pos = neg = 0
    shares = []
    focus = {}
    without_focus = {}
    for sym in uniq:
        keep = full_idx[symbols[full_idx] != sym]
        only = full_idx[symbols[full_idx] == sym]
        if only.size and abs(full_mean) > 1e-12:
            w = only.size / full_idx.size
            shares.append(abs(w * float(np.mean(mat["ret_bps"][only])) / full_mean))
        if keep.size:
            m = float(np.mean(mat["ret_bps"][keep]))
            if m > 0:
                pos += 1
            elif m < 0:
                neg += 1
    present = set(uniq)
    for s in FOCUS_SYMBOLS:
        if s not in present:
            focus[s] = "NOT_PRESENT"
            without_focus[f"without_{s}"] = "NOT_PRESENT"
        else:
            keep = full_idx[symbols[full_idx] != s]
            without_focus[f"without_{s}"] = (
                float(np.mean(mat["ret_bps"][keep])) if keep.size else None
            )
            focus[s] = "PRESENT"
    return {
        "max_symbol_contribution_share": max(shares) if shares else None,
        "positive_LOSO_count": pos,
        "negative_LOSO_count": neg,
        "focus": focus,
        "without": without_focus,
        "symbol_n_present": len(uniq),
    }


def candidate_balanced_view(rows: list[dict[str, Any]], keys: dict[str, str]) -> dict[str, Any]:
    """Equal weight per candidate mask."""
    def _med_share(field: str) -> tuple[Optional[float], Optional[float]]:
        vals = [r.get(field) for r in rows if r.get(field) is not None]
        if not vals:
            return None, None
        arr = np.asarray(vals, dtype=float)
        return float(np.median(arr)), float(np.mean(arr > 0))

    out = {}
    for label, field in keys.items():
        med, share = _med_share(field)
        out[f"median_{label}"] = med
        out[f"positive_{label}_share"] = share
    return out


def cluster_balanced_metric(
    *,
    mat: dict[str, np.ndarray],
    sel_mask: np.ndarray,
    clusters: np.ndarray,
    field: str = "ret_bps",
) -> Optional[float]:
    """Mean of per-cluster means (reduces repeated-event overweight)."""
    idx = np.where(sel_mask & mat["valid"])[0]
    if idx.size == 0:
        return None
    means = []
    for c in np.unique(clusters[idx]):
        m = idx[clusters[idx] == c]
        means.append(float(np.mean(mat[field][m])))
    return float(np.mean(means)) if means else None


def stress_status_from_views(
    *,
    cand_abs_med: Optional[float],
    cand_abs_share: Optional[float],
    clus_abs: Optional[float],
    cand_entry_med: Optional[float],
    cand_entry_share: Optional[float],
    clus_entry: Optional[float],
    cand_pers_med: Optional[float],
    cand_pers_share: Optional[float],
    clus_pers: Optional[float],
    prefix: str,
) -> str:
    """
    A/B/C each judged positive when BOTH candidate-balanced median and
    cluster-balanced metric are positive (when available).
    """
    def axis_pos(med, clus) -> Optional[bool]:
        flags = []
        if med is not None:
            flags.append(med > 0)
        if clus is not None:
            flags.append(clus > 0)
        if not flags:
            return None
        return all(flags)

    a = axis_pos(cand_abs_med, clus_abs)
    b = axis_pos(cand_entry_med, clus_entry)
    c = axis_pos(cand_pers_med, clus_pers)
    axes = [x for x in (a, b, c) if x is not None]
    if not axes:
        return f"{prefix}_STRESS_MIXED"
    if all(axes):
        return f"{prefix}_STRESS_COMPATIBLE"
    if all(not x for x in axes):
        return f"{prefix}_STRESS_CONTRADICTED"
    return f"{prefix}_STRESS_MIXED"


def program_decision(specific_status: str, family_status: str) -> dict[str, Any]:
    s_ok = specific_status.endswith("COMPATIBLE")
    f_ok = family_status.endswith("COMPATIBLE")
    s_mix = specific_status.endswith("MIXED")
    f_mix = family_status.endswith("MIXED")
    s_bad = specific_status.endswith("CONTRADICTED")
    f_bad = family_status.endswith("CONTRADICTED")

    if s_ok or f_ok:
        verdict = VERDICT_SUPPORT
        x29_v2 = True
        start_prospective = True
    elif s_mix or f_mix:
        verdict = VERDICT_MIXED
        x29_v2 = True
        start_prospective = True
    elif s_bad and f_bad:
        verdict = VERDICT_FAILURE
        x29_v2 = False
        start_prospective = False
    else:
        verdict = VERDICT_MIXED
        x29_v2 = True
        start_prospective = True
    return {
        "program_decision": verdict,
        "x29_v2_required": x29_v2,
        "start_prospective_on_current_logic": start_prospective,
        "specific_stress_status": specific_status,
        "family_stress_status": family_status,
        "cohort_membership_unchanged": True,
        "no_retune": True,
    }


def wide_stop_alert(stop_class_rows: list[dict[str, Any]]) -> Optional[str]:
    """Alert if Specific positive edge skewed to WIDE/VERY_WIDE and NORMAL non-positive."""
    by = {r["stop_risk_tag"]: r for r in stop_class_rows}
    normal = by.get("NORMAL_STOP") or {}
    wide = by.get("WIDE_STOP") or {}
    vwide = by.get("VERY_WIDE_STOP") or {}
    n_ret = normal.get("avg_return_bps")
    w_ret = wide.get("avg_return_bps")
    vw_ret = vwide.get("avg_return_bps")
    wide_pos = (w_ret is not None and w_ret > 0) or (vw_ret is not None and vw_ret > 0)
    normal_nonpos = n_ret is None or n_ret <= 0
    overall_pos = False
    for r in stop_class_rows:
        if (r.get("avg_return_bps") or 0) > 0:
            overall_pos = True
    if overall_pos and wide_pos and normal_nonpos:
        return "WIDE_STOP_EDGE_DEPENDENCY_ALERT"
    return None

"""Actual U/D tradeoff, Pareto, CURRENT dominance, predicted enrichment. No selection rule."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f, rank_group, session_of
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import spearman
from research.multiobjective_feasibility import ELIGIBLE_DAYS, JOINT_FEASIBLE_MIN_RATE, TOPK


def row_key(r: dict[str, Any]) -> str:
    return f"{r.get('date')}|{r.get('anchor')}|{r.get('symbol')}"


def cohort_key(r: dict[str, Any]) -> tuple[str, str, str]:
    return (str(r.get("date") or ""), session_of(r), str(r.get("anchor") or ""))


def mean_ud(rows: list[dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    us = [_f(r.get("T1")) for r in rows]
    ds = [_f(r.get("T2")) for r in rows]
    u = [float(v) for v in us if v is not None]
    d = [float(v) for v in ds if v is not None]
    return (float(np.mean(u)) if u else None, float(np.mean(d)) if d else None)


def pareto_mask(u: list[float], d: list[float]) -> list[bool]:
    uu = np.asarray(u, dtype=float)
    dd = np.asarray(d, dtype=float)
    n = int(uu.size)
    if n == 0:
        return []
    ge_u = uu[None, :] >= uu[:, None]
    ge_d = dd[None, :] >= dd[:, None]
    gt_u = uu[None, :] > uu[:, None]
    gt_d = dd[None, :] > dd[:, None]
    dom = ge_u & ge_d & (gt_u | gt_d)
    dominated = np.any(dom, axis=1)
    return [bool(x) for x in ~dominated]


def outcome_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if not r.get("common_cohort"):
            continue
        if _f(r.get("T1")) is None or _f(r.get("T2")) is None:
            continue
        out.append(r)
    return out


def current_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in outcome_rows(rows) if _f(r.get("current_score")) is not None]


def group_cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in rows:
        by[cohort_key(r)].append(r)
    return by


def actual_outcome_tradeoff(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(current_rows(rows))
    spears = []
    cohort_recs = []
    daily: dict[str, list[float]] = defaultdict(list)
    for (date, sess, an), grp in sorted(by.items()):
        if len(grp) < int(MIN_COHORT_N):
            continue
        u = [float(r["T1"]) for r in grp]
        d = [float(r["T2"]) for r in grp]
        sp = spearman(u, d)
        rec = {
            "date": date,
            "session": sess,
            "anchor": an,
            "n": len(grp),
            "OUTCOME_UD_SPEARMAN": sp,
        }
        cohort_recs.append(rec)
        if sp is None:
            continue
        spears.append(float(sp))
        daily[date].append(float(sp))
    neg = sum(1 for v in spears if v < 0)
    pos = sum(1 for v in spears if v > 0)
    daily_med = []
    for d in ELIGIBLE_DAYS:
        xs = daily.get(d) or []
        if not xs:
            continue
        daily_med.append({"date": d, "OUTCOME_UD_SPEARMAN_MEDIAN": float(np.median(xs)), "n_cohorts": len(xs)})
    return {
        "n_cohorts": len(cohort_recs),
        "OUTCOME_UD_SPEARMAN_MEAN": float(np.mean(spears)) if spears else None,
        "OUTCOME_UD_SPEARMAN_MEDIAN": float(np.median(spears)) if spears else None,
        "NEGATIVE_CORR_COHORT_N": neg,
        "POSITIVE_CORR_COHORT_N": pos,
        "ZERO_CORR_COHORT_N": sum(1 for v in spears if v == 0),
        "daily_median": daily_med,
        "cohorts": cohort_recs,
    }


def actual_pareto_and_current(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(current_rows(rows))
    sizes = []
    shares = []
    top3_n = 0
    dominated_n = 0
    any_dom_c = 0
    all_dom_c = 0
    joint_c = 0
    joint_cand = 0
    n_used = 0
    cohort_recs = []
    for (date, sess, an), grp in sorted(by.items()):
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_used += 1
        u = [float(r["T1"]) for r in grp]
        d = [float(r["T2"]) for r in grp]
        front = pareto_mask(u, d)
        fs = int(sum(1 for v in front if v))
        sizes.append(fs)
        shares.append(fs / float(len(grp)))
        ranked = rank_group(grp, "current_score")
        top = ranked[:TOPK]
        top3_n += len(top)
        idx = {row_key(r): i for i, r in enumerate(grp)}
        n_dom = 0
        for r in top:
            i = idx.get(row_key(r))
            if i is None:
                continue
            if not front[i]:
                n_dom += 1
        dominated_n += n_dom
        if n_dom > 0:
            any_dom_c += 1
        if top and n_dom == len(top):
            all_dom_c += 1
        cu, cd_ = mean_ud(top)
        n_joint = 0
        if cu is not None and cd_ is not None:
            for r in grp:
                if float(r["T1"]) > cu and float(r["T2"]) > cd_:
                    n_joint += 1
        if n_joint > 0:
            joint_c += 1
        joint_cand += n_joint
        cohort_recs.append(
            {
                "date": date,
                "session": sess,
                "anchor": an,
                "n": len(grp),
                "PARETO_FRONT_SIZE": fs,
                "PARETO_FRONT_SHARE": fs / float(len(grp)),
                "CURRENT_TOP3_N": len(top),
                "CURRENT_TOP3_DOMINATED_N": n_dom,
                "CURRENT_TOP3_MEAN_MFE": cu,
                "CURRENT_TOP3_MEAN_DOWNSIDE": cd_,
                "JOINT_IMPROVEMENT_CANDIDATE_N": n_joint,
                "JOINT_IMPROVEMENT_AVAILABLE": n_joint > 0,
            }
        )
    rate = (joint_c / float(n_used)) if n_used else None
    return {
        "n_cohorts": n_used,
        "PARETO_FRONT_SIZE_MEAN": float(np.mean(sizes)) if sizes else None,
        "PARETO_FRONT_SIZE_MEDIAN": float(np.median(sizes)) if sizes else None,
        "PARETO_FRONT_SHARE_MEAN": float(np.mean(shares)) if shares else None,
        "CURRENT_TOP3_N": top3_n,
        "CURRENT_TOP3_DOMINATED_N": dominated_n,
        "CURRENT_TOP3_DOMINATED_RATE": (dominated_n / float(top3_n)) if top3_n else None,
        "COHORTS_WITH_ANY_CURRENT_TOP3_DOMINATED": any_dom_c,
        "COHORTS_WITH_ALL_CURRENT_TOP3_DOMINATED": all_dom_c,
        "JOINT_IMPROVEMENT_AVAILABLE_COHORT_N": joint_c,
        "JOINT_IMPROVEMENT_AVAILABLE_RATE": rate,
        "JOINT_IMPROVEMENT_CANDIDATE_N": joint_cand,
        "cohorts": cohort_recs,
    }


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.mean(xs))


def aggregate_predicted(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    sp_med = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_T1_T2_SPEARMAN_MEDIAN"))) is not None]
    ov_med = [float(v) for b in bodies if (v := _f(b.get("TOP3_OVERLAP_RATE_MEDIAN"))) is not None]
    psz = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_PARETO_SIZE_MEAN"))) is not None]
    psh = [float(v) for b in bodies if (v := _f(b.get("PREDICTED_PARETO_SHARE_MEAN"))) is not None]
    by_day_mfe: dict[str, list[float]] = defaultdict(list)
    by_day_dn: dict[str, list[float]] = defaultdict(list)
    spec_rows = []
    for b in bodies:
        spec_rows.append(
            {
                "spec_id": b.get("spec_id"),
                "feature_set": b.get("feature_set"),
                "normalization": b.get("normalization"),
                "alpha": b.get("alpha"),
                "MATCHED_ROWS": b.get("MATCHED_ROWS"),
                "n_cohorts": b.get("n_cohorts"),
                "PREDICTED_T1_T2_SPEARMAN_MEDIAN": b.get("PREDICTED_T1_T2_SPEARMAN_MEDIAN"),
                "PREDICTED_T1_T2_SPEARMAN_MIN": b.get("PREDICTED_T1_T2_SPEARMAN_MIN"),
                "PREDICTED_T1_T2_SPEARMAN_MAX": b.get("PREDICTED_T1_T2_SPEARMAN_MAX"),
                "TOP3_OVERLAP_RATE_MEDIAN": b.get("TOP3_OVERLAP_RATE_MEDIAN"),
                "PREDICTED_PARETO_SIZE_MEAN": b.get("PREDICTED_PARETO_SIZE_MEAN"),
                "PREDICTED_PARETO_SHARE_MEAN": b.get("PREDICTED_PARETO_SHARE_MEAN"),
            }
        )
        for rec in b.get("daily_mfe_delta") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_day_mfe[str(rec.get("date"))].append(float(v))
        for rec in b.get("daily_downside_delta") or []:
            v = _f(rec.get("value"))
            if v is None:
                continue
            by_day_dn[str(rec.get("date"))].append(float(v))
    cons_mfe = []
    cons_dn = []
    daily = []
    for d in ELIGIBLE_DAYS:
        xm = by_day_mfe.get(d) or []
        xd = by_day_dn.get(d) or []
        if not xm or not xd:
            continue
        mm = float(np.median(xm))
        md = float(np.median(xd))
        cons_mfe.append(mm)
        cons_dn.append(md)
        daily.append({"date": d, "PARETO_MFE_DELTA_VS_CURRENT": mm, "PARETO_DOWNSIDE_DELTA_VS_CURRENT": md})
    mfe_mean = _mean(cons_mfe)
    dn_mean = _mean(cons_dn)
    return {
        "SPEC_N": len(bodies),
        "MEDIAN_T1_T2_PREDICTED_SPEARMAN": _median(sp_med),
        "T1_T2_PREDICTED_SPEARMAN_MIN": float(np.min(sp_med)) if sp_med else None,
        "T1_T2_PREDICTED_SPEARMAN_MAX": float(np.max(sp_med)) if sp_med else None,
        "MEDIAN_T1_T2_TOP3_OVERLAP_RATE": _median(ov_med),
        "PREDICTED_PARETO_SIZE_MEAN": _mean(psz),
        "PREDICTED_PARETO_SHARE_MEAN": _mean(psh),
        "PARETO_MFE_DELTA_VS_CURRENT": mfe_mean,
        "PARETO_DOWNSIDE_DELTA_VS_CURRENT": dn_mean,
        "PARETO_MFE_POSITIVE_DAYS": sum(1 for v in cons_mfe if v > 0),
        "PARETO_DOWNSIDE_POSITIVE_DAYS": sum(1 for v in cons_dn if v > 0),
        "PARETO_MFE_NEGATIVE_DAYS": sum(1 for v in cons_mfe if v < 0),
        "PARETO_DOWNSIDE_NEGATIVE_DAYS": sum(1 for v in cons_dn if v < 0),
        "daily": daily,
        "spec_rows": spec_rows,
    }


def predicted_joint_enrich(pred: dict[str, Any]) -> bool:
    a = pred.get("PARETO_MFE_DELTA_VS_CURRENT")
    b = pred.get("PARETO_DOWNSIDE_DELTA_VS_CURRENT")
    return bool(a is not None and b is not None and float(a) > 0 and float(b) > 0)


def decide(*, joint_rate: Optional[float], pred_joint: bool) -> dict[str, Any]:
    if joint_rate is None:
        return {
            "CASE": None,
            "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_FAILED",
            "JOINT_OBJECTIVE_FEASIBLE": False,
            "PRIMARY_MECHANISM": "Joint-improvement rate could not be computed.",
            "NEXT_RESEARCH": "NONE",
            "note": "STOP. Metric missing.",
        }
    rate_ok = float(joint_rate) >= float(JOINT_FEASIBLE_MIN_RATE)
    if rate_ok and pred_joint:
        return {
            "CASE": "A",
            "VERDICT": "JOINT_OBJECTIVE_FEASIBLE",
            "JOINT_OBJECTIVE_FEASIBLE": True,
            "PRIMARY_MECHANISM": (
                "Actual joint improvement vs CURRENT Top3 centroid exists in >=50% of cohorts, "
                "and predicted T1/T2 Pareto members enrich both actual MFE and downside vs CURRENT. "
                "Oracle Pareto is diagnostic only and is not a runtime rule."
            ),
            "NEXT_RESEARCH": "PARETO_ENTRY_ARCHITECTURE_PRECOMMIT_DESIGN",
            "note": "CASE A. STOP. No Pareto selection rule this run.",
        }
    if rate_ok and not pred_joint:
        return {
            "CASE": "B",
            "VERDICT": "JOINT_OPPORTUNITY_EXISTS_MODEL_CANNOT_IDENTIFY",
            "JOINT_OBJECTIVE_FEASIBLE": False,
            "PRIMARY_MECHANISM": (
                "Future paths contain candidates that beat CURRENT Top3 on both U and D, "
                "but existing T1/T2 Ridge scores do not concentrate that joint region."
            ),
            "NEXT_RESEARCH": "MULTI_OUTPUT_MODEL_ARCHITECTURE_REDESIGN",
            "note": "CASE B. STOP. No new model this run.",
        }
    if (not rate_ok) and pred_joint:
        return {
            "CASE": "D",
            "VERDICT": "MULTI_OBJECTIVE_FEASIBILITY_INCONCLUSIVE",
            "JOINT_OBJECTIVE_FEASIBLE": False,
            "PRIMARY_MECHANISM": (
                "Predicted Pareto looks jointly enriched vs CURRENT, but actual joint-improvement "
                "availability is below the frozen 50% rate. Mixed evidence."
            ),
            "NEXT_RESEARCH": "NONE",
            "note": "CASE D. STOP.",
        }
    return {
        "CASE": "C",
        "VERDICT": "STRUCTURAL_UPSIDE_DOWNSIDE_TRADEOFF",
        "JOINT_OBJECTIVE_FEASIBLE": False,
        "PRIMARY_MECHANISM": (
            "Actual joint improvement vs CURRENT Top3 centroid is rare (<50% of cohorts). "
            "U and D on the future path are a structural tradeoff. Do not target both-better."
        ),
        "NEXT_RESEARCH": "RISK_RETURN_PREFERENCE_PRECOMMIT_DESIGN",
        "note": "CASE C. STOP. Do not chase joint improvement.",
    }

"""Fillable geometry, post-fill quality, enrichment, CASE A-D. No model."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_execution_feasibility.analyze import (
    fill_counts,
    fillable_joint_availability,
    labeled_rows,
)
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.execution_aware_target_feasibility import (
    FILL_EXPECTED,
    PARITY_ABS_TOL,
    RATE_MIN,
    SUPPORT_MIN_DAY_N,
    SUPPORT_MIN_LODO_TRAIN_N,
)
from research.multiobjective_feasibility.analyze import group_cohorts, pareto_mask


def _close(a: Any, b: Any, tol: float) -> bool:
    x, y = _f(a), _f(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) <= float(tol)


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(np.median(xs))


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.quantile(np.asarray(xs, dtype=float), q))


def fill_parity(counts: dict[str, Any], fillable: dict[str, Any]) -> dict[str, Any]:
    obs = {
        "ALL_CANDIDATE_N": counts.get("ALL_CANDIDATE_N"),
        "ALL_WOULD_FILL_N": counts.get("ALL_WOULD_FILL_N"),
        "ALL_WOULD_FILL_RATE": counts.get("ALL_WOULD_FILL_RATE"),
        "JOINT_POSITIVE_WOULD_FILL_N": counts.get("JOINT_POSITIVE_WOULD_FILL_N"),
        "CURRENT_TOP3_WOULD_FILL_N": counts.get("CURRENT_TOP3_WOULD_FILL_N"),
        "FILLABLE_JOINT_AVAILABLE_COHORT_N": fillable.get("FILLABLE_JOINT_AVAILABLE_COHORT_N"),
        "FILLABLE_JOINT_AVAILABLE_RATE": fillable.get("FILLABLE_JOINT_AVAILABLE_RATE"),
    }
    checks = {
        "ALL_CANDIDATE_N": int(obs["ALL_CANDIDATE_N"] or -1) == int(FILL_EXPECTED["ALL_CANDIDATE_N"]),
        "ALL_WOULD_FILL_N": int(obs["ALL_WOULD_FILL_N"] or -1) == int(FILL_EXPECTED["ALL_WOULD_FILL_N"]),
        "ALL_WOULD_FILL_RATE": _close(obs["ALL_WOULD_FILL_RATE"], FILL_EXPECTED["ALL_WOULD_FILL_RATE"], PARITY_ABS_TOL),
        "JOINT_POSITIVE_WOULD_FILL_N": int(obs["JOINT_POSITIVE_WOULD_FILL_N"] or -1)
        == int(FILL_EXPECTED["JOINT_POSITIVE_WOULD_FILL_N"]),
        "CURRENT_TOP3_WOULD_FILL_N": int(obs["CURRENT_TOP3_WOULD_FILL_N"] or -1)
        == int(FILL_EXPECTED["CURRENT_TOP3_WOULD_FILL_N"]),
        "FILLABLE_JOINT_AVAILABLE_COHORT_N": int(obs["FILLABLE_JOINT_AVAILABLE_COHORT_N"] or -1)
        == int(FILL_EXPECTED["FILLABLE_JOINT_AVAILABLE_COHORT_N"]),
        "FILLABLE_JOINT_AVAILABLE_RATE": _close(
            obs["FILLABLE_JOINT_AVAILABLE_RATE"],
            FILL_EXPECTED["FILLABLE_JOINT_AVAILABLE_RATE"],
            PARITY_ABS_TOL,
        ),
    }
    return {"BASE_PARITY": all(checks.values()), "checks": checks, "expected": dict(FILL_EXPECTED), "observed": obs}


def fillable_geometry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    ns: list[int] = []
    n0 = n1 = n2 = n3 = 0
    n_coh = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_coh += 1
        nf = int(sum(1 for r in grp if r.get("WOULD_FILL")))
        ns.append(nf)
        if nf == 0:
            n0 += 1
        elif nf == 1:
            n1 += 1
        elif nf == 2:
            n2 += 1
        else:
            n3 += 1
    any_n = n1 + n2 + n3
    multi_n = n2 + n3
    xs = [float(v) for v in ns]
    return {
        "COHORT_N": n_coh,
        "FILLABLE_0_COHORT_N": n0,
        "FILLABLE_1_COHORT_N": n1,
        "FILLABLE_2_COHORT_N": n2,
        "FILLABLE_3PLUS_COHORT_N": n3,
        "FILLABLE_ANY_COHORT_N": any_n,
        "FILLABLE_ANY_COHORT_RATE": _rate(any_n, n_coh),
        "MULTI_FILLABLE_COHORT_N": multi_n,
        "MULTI_FILLABLE_COHORT_RATE": _rate(multi_n, n_coh),
        "FILLABLE_PER_COHORT_MEAN": float(np.mean(xs)) if xs else None,
        "FILLABLE_PER_COHORT_MEDIAN": _median(xs),
        "FILLABLE_PER_COHORT_P90": _pct(xs, 0.90),
    }


def attach_postfill(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        r["POSTFILL_MFE_600"] = _f(r.get("MFE_FROM_FILL"))
        r["POSTFILL_DOWNSIDE_AVOID_600"] = _f(r.get("DOWNSIDE_FROM_FILL"))


def postfill_pareto(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    sizes = []
    pareto_n = 0
    pareto_coh = 0
    multi_dom = 0
    multi_complete = 0
    incomplete = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        filled = [r for r in grp if r.get("WOULD_FILL")]
        complete = [
            r
            for r in filled
            if _f(r.get("POSTFILL_MFE_600")) is not None and _f(r.get("POSTFILL_DOWNSIDE_AVOID_600")) is not None
        ]
        incomplete += len(filled) - len(complete)
        if not complete:
            continue
        u = [float(r["POSTFILL_MFE_600"]) for r in complete]
        d = [float(r["POSTFILL_DOWNSIDE_AVOID_600"]) for r in complete]
        front = pareto_mask(u, d)
        fs = int(sum(1 for v in front if v))
        sizes.append(fs)
        pareto_n += fs
        pareto_coh += 1
        if len(complete) >= 2:
            multi_complete += 1
            if fs < len(complete):
                multi_dom += 1
    return {
        "FILLABLE_PARETO_N": pareto_n,
        "FILLABLE_PARETO_COHORT_N": pareto_coh,
        "FILLABLE_PARETO_SIZE_MEAN": float(np.mean(sizes)) if sizes else None,
        "MULTI_FILLABLE_WITH_DOMINANCE_COHORT_N": multi_dom,
        "MULTI_FILLABLE_COMPLETE_COHORT_N": multi_complete,
        "MULTI_FILLABLE_WITH_DOMINANCE_RATE": _rate(multi_dom, multi_complete),
        "POSTFILL_INCOMPLETE_N": incomplete,
    }


def current_enrichment(rows: list[dict[str, Any]], *, all_rate: Optional[float], top3_rate: Optional[float]) -> dict[str, Any]:
    enr = None
    if all_rate is not None and float(all_rate) > 0 and top3_rate is not None:
        enr = float(top3_rate) / float(all_rate)
    by = group_cohorts(labeled_rows(rows))
    daily: dict[str, dict[str, int]] = {d: {"all_n": 0, "all_f": 0, "top_n": 0, "top_f": 0} for d in ELIGIBLE_DAYS}
    for (date, _s, _a), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        bucket = daily.get(str(date))
        if bucket is None:
            continue
        for r in grp:
            bucket["all_n"] += 1
            if r.get("WOULD_FILL"):
                bucket["all_f"] += 1
            if r.get("is_current_top3"):
                bucket["top_n"] += 1
                if r.get("WOULD_FILL"):
                    bucket["top_f"] += 1
    pos = 0
    neg = 0
    day_rows = []
    for d in ELIGIBLE_DAYS:
        b = daily[d]
        ar = _rate(b["all_f"], b["all_n"])
        tr = _rate(b["top_f"], b["top_n"])
        de = None if ar is None or ar <= 0 or tr is None else float(tr) / float(ar)
        if de is not None and de > 1:
            pos += 1
        elif de is not None and de < 1:
            neg += 1
        day_rows.append({"date": d, "ALL_WOULD_FILL_RATE": ar, "CURRENT_TOP3_WOULD_FILL_RATE": tr, "ENRICHMENT": de})
    return {
        "CURRENT_FILL_ENRICHMENT": enr,
        "CURRENT_FILL_ENRICHMENT_POS_DAYS": pos,
        "CURRENT_FILL_ENRICHMENT_NEG_DAYS": neg,
        "daily": day_rows,
    }


def mechanism(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    f0 = f1 = f2 = f3 = other = 0
    for r in lab:
        if r.get("WOULD_FILL"):
            f2 += 1
            continue
        klass = str(r.get("nonfill_class") or "OTHER")
        if klass == "NO_VALID_CONTINUOUS_BOARD":
            f0 += 1
        elif klass == "NO_ASK_CROSS_WITHIN_WAIT":
            f1 += 1
        elif klass == "BOARD_BECAME_NONEXECUTABLE":
            f3 += 1
        else:
            other += 1
    valid = f1 + f2 + f3
    cross_den = f1 + f2
    return {
        "F0_NO_VALID_BOARD_N": f0,
        "F1_NO_ASK_CROSS_N": f1,
        "F2_ASK_CROSS_FILL_N": f2,
        "F3_BECAME_NONEXEC_N": f3,
        "F_OTHER_N": other,
        "VALID_BOARD_WITHIN_WAIT_N": valid,
        "VALID_BOARD_WITHIN_WAIT_RATE": _rate(valid, len(lab)),
        "ASK_CROSS_GIVEN_VALID_BOARD_N": f2,
        "ASK_CROSS_GIVEN_VALID_BOARD_RATE": _rate(f2, cross_den),
        "WOULD_FILL_N": f2,
    }


def executed_old_joint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    xs = [r for r in lab if r.get("WOULD_FILL") and int(r.get("joint_label") or 0) == 1]
    by_day: dict[str, int] = {d: 0 for d in ELIGIBLE_DAYS}
    for r in xs:
        d = str(r.get("date") or "")
        if d in by_day:
            by_day[d] += 1
    counts = [float(by_day[d]) for d in ELIGIBLE_DAYS]
    return {
        "EXECUTED_OLD_JOINT_N": len(xs),
        "EXECUTED_OLD_JOINT_RATE_ALL": _rate(len(xs), len(lab)),
        "EXECUTED_OLD_JOINT_PER_DAY_MEDIAN": _median(counts),
        "MIN_EXECUTED_OLD_JOINT_PER_DAY": int(min(counts)) if counts else None,
        "MAX_EXECUTED_OLD_JOINT_PER_DAY": int(max(counts)) if counts else None,
        "daily": [{"date": d, "EXECUTED_OLD_JOINT_N": by_day[d]} for d in ELIGIBLE_DAYS],
    }


def fillable_support(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in labeled_rows(rows) if r.get("WOULD_FILL")]
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in ELIGIBLE_DAYS}
    for r in filled:
        d = str(r.get("date") or "")
        if d in by_day:
            by_day[d].append(r)
    day_n = [len(by_day[d]) for d in ELIGIBLE_DAYS]
    total = len(filled)
    lodo = []
    for d in ELIGIBLE_DAYS:
        train_n = total - len(by_day[d])
        lodo.append({"holdout": d, "TRAIN_FILLABLE_N": train_n, "HOLDOUT_FILLABLE_N": len(by_day[d])})
    train_ns = [int(r["TRAIN_FILLABLE_N"]) for r in lodo]
    mfe_ok = [float(v) for r in filled if (v := _f(r.get("POSTFILL_MFE_600"))) is not None]
    dn_ok = [float(v) for r in filled if (v := _f(r.get("POSTFILL_DOWNSIDE_AVOID_600"))) is not None]
    return {
        "FILLABLE_TRAINING_N": total,
        "FILLABLE_DAY_MIN_N": min(day_n) if day_n else None,
        "FILLABLE_DAY_MEDIAN_N": int(_median([float(v) for v in day_n]) or 0) if day_n else None,
        "FILLABLE_DAY_MAX_N": max(day_n) if day_n else None,
        "MIN_LODO_TRAIN_FILLABLE_N": min(train_ns) if train_ns else None,
        "POSTFILL_MFE_MEDIAN": _median(mfe_ok),
        "POSTFILL_DOWNSIDE_MEDIAN": _median(dn_ok),
        "POSTFILL_MFE_N": len(mfe_ok),
        "POSTFILL_DOWNSIDE_N": len(dn_ok),
        "daily": [{"date": d, "FILLABLE_N": len(by_day[d])} for d in ELIGIBLE_DAYS],
        "lodo": lodo,
        "SUPPORT_OK": bool(
            total >= int(FILL_EXPECTED["ALL_WOULD_FILL_N"])
            and (min(train_ns) if train_ns else 0) >= int(SUPPORT_MIN_LODO_TRAIN_N)
            and (min(day_n) if day_n else 0) >= int(SUPPORT_MIN_DAY_N)
        ),
    }


def decide(
    *,
    parity_ok: bool,
    geom: dict[str, Any],
    support: dict[str, Any],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "EXECUTION_AWARE_TARGET_FEASIBILITY_INTEGRITY_FAILED",
            "TARGET_ARCHITECTURE": None,
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen fill-count parity vs JOINT_OPPORTUNITY_EXECUTION_FEASIBILITY failed.",
            "note": "STOP. BASE_PARITY failed.",
        }
    any_r = _f(geom.get("FILLABLE_ANY_COHORT_RATE"))
    multi_r = _f(geom.get("MULTI_FILLABLE_COHORT_RATE"))
    support_ok = bool(support.get("SUPPORT_OK"))
    if any_r is None or multi_r is None:
        return {
            "CASE": "D",
            "VERDICT": "EXECUTION_AWARE_TARGET_SUPPORT_INSUFFICIENT",
            "TARGET_ARCHITECTURE": None,
            "PRIMARY_MECHANISM": "SAMPLE_SUPPORT_INSUFFICIENT",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": "Fillable cohort geometry could not be computed.",
            "note": "CASE D. STOP.",
        }
    if float(any_r) < float(RATE_MIN):
        return {
            "CASE": "C",
            "VERDICT": "PASSIVE_EXECUTION_OPPORTUNITY_TOO_SPARSE",
            "TARGET_ARCHITECTURE": "NONE",
            "PRIMARY_MECHANISM": "PASSIVE_FILL_CANDIDATE_OPPORTUNITY_SPARSE",
            "NEXT_RESEARCH": "EXECUTION_POLICY_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Current Passive Fill produces a fillable candidate in fewer than 50% of cohorts. "
                "A ranking/target model cannot create opportunity the fill rule does not admit."
            ),
            "note": "CASE C. STOP. Execution policy not changed this run.",
        }
    if float(multi_r) < float(RATE_MIN):
        return {
            "CASE": "B",
            "VERDICT": "FILLABILITY_IS_PRIMARY_ENTRY_BOTTLENECK",
            "TARGET_ARCHITECTURE": "FILLABILITY_FIRST",
            "PRIMARY_MECHANISM": "FILL_VS_EXPIRE_DOMINATES_CROSS_SECTION",
            "NEXT_RESEARCH": "FILLABILITY_TARGET_PRECOMMIT_DESIGN",
            "PRIMARY_FINDING": (
                "Most cohorts have at most one Passive-Fill candidate. Post-fill quality ranking "
                "is secondary to identifying who fills."
            ),
            "note": "CASE B. STOP. Fillability target not started this run.",
        }
    if not support_ok:
        return {
            "CASE": "D",
            "VERDICT": "EXECUTION_AWARE_TARGET_SUPPORT_INSUFFICIENT",
            "TARGET_ARCHITECTURE": "TWO_STAGE_EXECUTION_QUALITY",
            "PRIMARY_MECHANISM": "SAMPLE_SUPPORT_INSUFFICIENT",
            "NEXT_RESEARCH": "ENTRY_RESEARCH_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Multi-fillable geometry exists, but fillable sample support is too thin to precommit "
                "a conditional quality target."
            ),
            "note": "CASE D. STOP.",
        }
    return {
        "CASE": "A",
        "VERDICT": "EXECUTION_AWARE_TWO_STAGE_TARGET_FEASIBLE",
        "TARGET_ARCHITECTURE": "TWO_STAGE_EXECUTION_QUALITY",
        "PRIMARY_MECHANISM": "FILL_THEN_CONDITIONAL_QUALITY",
        "NEXT_RESEARCH": "EXECUTION_AWARE_TARGET_PRECOMMIT_DEVELOPMENT",
        "PRIMARY_FINDING": (
            "Current Passive Fill leaves multi-name cross-sectional choice in most cohorts, "
            "with enough fillable sample to support a two-stage fillability then quality target. "
            "No model trained this run."
        ),
        "note": "CASE A. STOP. Two-stage target development not started this run.",
    }

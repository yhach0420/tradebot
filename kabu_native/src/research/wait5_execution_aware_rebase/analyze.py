"""W5 population, fillability, fill-aligned quality, CURRENT diagnostics, CASE A-D."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f, rank_group, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.entry_execution_feasibility.analyze import labeled_rows
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import spearman
from research.execution_aware_target_feasibility.analyze import fillable_geometry
from research.multiobjective_feasibility.analyze import group_cohorts, pareto_mask
from research.passive_wait_policy_reassessment.analyze import (
    _close,
    _mean,
    _median,
    _pct,
    _rate,
    survival,
    wait_body,
)
from research.wait5_execution_aware_rebase import (
    DEV_WAIT_ID,
    DOMINANCE_MIN_RATE,
    PARITY_ABS_TOL,
    PARITY_EXPECTED,
    QUALITY_SPEARMAN_WEAK_MAX,
    RATE_MIN,
)


def attach_w5(rows: list[dict[str, Any]]) -> None:
    for r in labeled_rows(rows):
        w = wait_body(r, DEV_WAIT_ID)
        filled = bool(w.get("WOULD_FILL"))
        r["Y_FILL5"] = 1 if filled else 0
        r["WOULD_FILL"] = filled
        r["U_FILL"] = w.get("POSTFILL_MFE_600")
        r["D_FILL"] = w.get("POSTFILL_DOWNSIDE_AVOID_600")
        r["POSTFILL_MFE_600"] = r["U_FILL"]
        r["POSTFILL_DOWNSIDE_AVOID_600"] = r["D_FILL"]
        r["TIME_TO_FILL_SEC"] = w.get("TIME_TO_FILL_SEC")
        r["nonfill_class"] = w.get("nonfill_class")


def freeze_parity(geom: dict[str, Any], *, fill_n: int, labeled_n: int, survival_rate: Any, pm_multi: Any) -> dict[str, Any]:
    obs = {
        "ALL_CANDIDATE_N": labeled_n,
        "W5_WOULD_FILL_N": fill_n,
        "W5_ANY_RATE": geom.get("FILLABLE_ANY_COHORT_RATE"),
        "W5_MULTI_RATE": geom.get("MULTI_FILLABLE_COHORT_RATE"),
        "W5_FILLABLE_0_COHORT_N": geom.get("FILLABLE_0_COHORT_N"),
        "W5_FILLABLE_1_COHORT_N": geom.get("FILLABLE_1_COHORT_N"),
        "W5_FILLABLE_2_COHORT_N": geom.get("FILLABLE_2_COHORT_N"),
        "W5_FILLABLE_3PLUS_COHORT_N": geom.get("FILLABLE_3PLUS_COHORT_N"),
        "W5_JOINT_SURVIVAL": survival_rate,
        "PM_MULTI_RATE": pm_multi,
    }
    checks = {
        "ALL_CANDIDATE_N": int(obs["ALL_CANDIDATE_N"] or -1) == int(PARITY_EXPECTED["ALL_CANDIDATE_N"]),
        "W5_WOULD_FILL_N": int(obs["W5_WOULD_FILL_N"] or -1) == int(PARITY_EXPECTED["W5_WOULD_FILL_N"]),
        "W5_ANY_RATE": _close(obs["W5_ANY_RATE"], PARITY_EXPECTED["W5_ANY_RATE"], PARITY_ABS_TOL),
        "W5_MULTI_RATE": _close(obs["W5_MULTI_RATE"], PARITY_EXPECTED["W5_MULTI_RATE"], PARITY_ABS_TOL),
        "W5_FILLABLE_0_COHORT_N": int(obs["W5_FILLABLE_0_COHORT_N"] or -1)
        == int(PARITY_EXPECTED["W5_FILLABLE_0_COHORT_N"]),
        "W5_FILLABLE_1_COHORT_N": int(obs["W5_FILLABLE_1_COHORT_N"] or -1)
        == int(PARITY_EXPECTED["W5_FILLABLE_1_COHORT_N"]),
        "W5_FILLABLE_2_COHORT_N": int(obs["W5_FILLABLE_2_COHORT_N"] or -1)
        == int(PARITY_EXPECTED["W5_FILLABLE_2_COHORT_N"]),
        "W5_FILLABLE_3PLUS_COHORT_N": int(obs["W5_FILLABLE_3PLUS_COHORT_N"] or -1)
        == int(PARITY_EXPECTED["W5_FILLABLE_3PLUS_COHORT_N"]),
        "W5_JOINT_SURVIVAL": _close(obs["W5_JOINT_SURVIVAL"], PARITY_EXPECTED["W5_JOINT_SURVIVAL"], PARITY_ABS_TOL),
        "PM_MULTI_RATE": _close(obs["PM_MULTI_RATE"], PARITY_EXPECTED["PM_MULTI_RATE"], PARITY_ABS_TOL),
    }
    return {"BASE_PARITY": all(checks.values()), "checks": checks, "expected": dict(PARITY_EXPECTED), "observed": obs}


def _ge(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return float(a) + 1e-12 >= float(b)


def threeplus_rate(geom: dict[str, Any]) -> Optional[float]:
    return _rate(int(geom.get("FILLABLE_3PLUS_COHORT_N") or 0), int(geom.get("COHORT_N") or 0))


def session_capacity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    out: dict[str, Any] = {}
    for sess in ("AM", "PM"):
        grp = [r for r in lab if session_of(r) == sess]
        geom = fillable_geometry(grp)
        out[sess] = {
            "session": sess,
            "LABELED_N": len(grp),
            "Y_FILL5_POS_N": sum(1 for r in grp if int(r.get("Y_FILL5") or 0) == 1),
            "Y_FILL5_RATE": _rate(sum(1 for r in grp if int(r.get("Y_FILL5") or 0) == 1), len(grp)),
            **geom,
            "THREEPLUS_RATE": threeplus_rate(geom),
        }
    return out


def y_fill5_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    pos = [r for r in lab if int(r.get("Y_FILL5") or 0) == 1]
    daily = []
    for d in ELIGIBLE_DAYS:
        xs = [r for r in lab if str(r.get("date") or "") == d]
        npos = sum(1 for r in xs if int(r.get("Y_FILL5") or 0) == 1)
        daily.append({"date": d, "N": len(xs), "Y_FILL5_POS_N": npos, "Y_FILL5_RATE": _rate(npos, len(xs))})
    return {
        "Y_FILL5_POS_N": len(pos),
        "Y_FILL5_NEG_N": len(lab) - len(pos),
        "Y_FILL5_RATE": _rate(len(pos), len(lab)),
        "daily": daily,
    }


def _dist(xs: list[float]) -> dict[str, Any]:
    return {
        "N": len(xs),
        "mean": _mean(xs),
        "median": _median(xs),
        "p25": _pct(xs, 0.25),
        "p75": _pct(xs, 0.75),
    }


def fill_aligned(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in labeled_rows(rows) if int(r.get("Y_FILL5") or 0) == 1]
    u = [float(v) for r in filled if (v := _f(r.get("U_FILL"))) is not None]
    d = [float(v) for r in filled if (v := _f(r.get("D_FILL"))) is not None]
    body: dict[str, Any] = {
        "U_FILL_N": len(u),
        "D_FILL_N": len(d),
        "U_FILL": _dist(u),
        "D_FILL": _dist(d),
        "by_session": {},
    }
    for sess in ("AM", "PM"):
        xs = [r for r in filled if session_of(r) == sess]
        uu = [float(v) for r in xs if (v := _f(r.get("U_FILL"))) is not None]
        dd = [float(v) for r in xs if (v := _f(r.get("D_FILL"))) is not None]
        body["by_session"][sess] = {"U_FILL": _dist(uu), "D_FILL": _dist(dd)}
    return body


def postfill_geometry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = group_cohorts(labeled_rows(rows))
    sizes: list[int] = []
    pareto_n = 0
    pareto_coh = 0
    multi_dom = 0
    multi_complete = 0
    three_dom = 0
    three_complete = 0
    incomplete = 0
    for _k, grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        filled = [r for r in grp if int(r.get("Y_FILL5") or 0) == 1]
        complete = [
            r for r in filled if _f(r.get("U_FILL")) is not None and _f(r.get("D_FILL")) is not None
        ]
        incomplete += len(filled) - len(complete)
        if not complete:
            continue
        u = [float(r["U_FILL"]) for r in complete]
        d = [float(r["D_FILL"]) for r in complete]
        front = pareto_mask(u, d)
        fs = int(sum(1 for v in front if v))
        sizes.append(fs)
        pareto_n += fs
        pareto_coh += 1
        if len(complete) >= 2:
            multi_complete += 1
            if fs < len(complete):
                multi_dom += 1
        if len(complete) >= 3:
            three_complete += 1
            if fs < len(complete):
                three_dom += 1
    geom = fillable_geometry(rows)
    return {
        "FILLABLE_PARETO_N": pareto_n,
        "FILLABLE_PARETO_COHORT_N": pareto_coh,
        "FILLABLE_PARETO_SIZE_MEAN": float(np.mean(sizes)) if sizes else None,
        "MULTI_FILLABLE_COHORT_N": geom.get("MULTI_FILLABLE_COHORT_N"),
        "MULTI_FILLABLE_WITH_DOMINANCE_N": multi_dom,
        "MULTI_FILLABLE_COMPLETE_COHORT_N": multi_complete,
        "MULTI_FILLABLE_WITH_DOMINANCE_RATE": _rate(multi_dom, multi_complete),
        "THREEPLUS_WITH_DOMINANCE_N": three_dom,
        "THREEPLUS_COMPLETE_COHORT_N": three_complete,
        "THREEPLUS_WITH_DOMINANCE_RATE": _rate(three_dom, three_complete),
        "POSTFILL_INCOMPLETE_N": incomplete,
    }


def current_fillability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lab = labeled_rows(rows)
    all_n = len(lab)
    all_f = sum(1 for r in lab if int(r.get("Y_FILL5") or 0) == 1)
    all_rate = _rate(all_f, all_n)
    by = group_cohorts(lab)
    t1_n = t1_f = t3_n = t3_f = t5_n = t5_f = 0
    daily: dict[str, dict[str, int]] = {
        d: {"all_n": 0, "all_f": 0, "t3_n": 0, "t3_f": 0} for d in ELIGIBLE_DAYS
    }
    sess_b = {s: {"all_n": 0, "all_f": 0, "t3_n": 0, "t3_f": 0} for s in ("AM", "PM")}
    for (date, sess, _an), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, "current_score")
        for i, r in enumerate(ranked):
            filled = int(r.get("Y_FILL5") or 0) == 1
            if i < 1:
                t1_n += 1
                t1_f += int(filled)
            if i < 3:
                t3_n += 1
                t3_f += int(filled)
            if i < 5:
                t5_n += 1
                t5_f += int(filled)
            db = daily.get(str(date))
            if db is not None:
                db["all_n"] += 1
                db["all_f"] += int(filled)
                if i < 3:
                    db["t3_n"] += 1
                    db["t3_f"] += int(filled)
            sb = sess_b.get(str(sess))
            if sb is not None:
                sb["all_n"] += 1
                sb["all_f"] += int(filled)
                if i < 3:
                    sb["t3_n"] += 1
                    sb["t3_f"] += int(filled)
    t3_rate = _rate(t3_f, t3_n)
    enr = None if all_rate is None or float(all_rate) <= 0 or t3_rate is None else float(t3_rate) / float(all_rate)
    day_rows = []
    for d in ELIGIBLE_DAYS:
        b = daily[d]
        ar = _rate(b["all_f"], b["all_n"])
        tr = _rate(b["t3_f"], b["t3_n"])
        de = None if ar is None or ar <= 0 or tr is None else float(tr) / float(ar)
        day_rows.append({"date": d, "ALL_FILL5_RATE": ar, "CURRENT_TOP3_FILL5_RATE": tr, "ENRICHMENT": de})
    sess_rows = {}
    for s in ("AM", "PM"):
        b = sess_b[s]
        ar = _rate(b["all_f"], b["all_n"])
        tr = _rate(b["t3_f"], b["t3_n"])
        de = None if ar is None or ar <= 0 or tr is None else float(tr) / float(ar)
        sess_rows[s] = {"ALL_FILL5_RATE": ar, "CURRENT_TOP3_FILL5_RATE": tr, "ENRICHMENT": de}
    return {
        "ALL_FILL5_RATE": all_rate,
        "CURRENT_TOP1_FILL5_RATE": _rate(t1_f, t1_n),
        "CURRENT_TOP3_FILL5_RATE": t3_rate,
        "CURRENT_TOP5_FILL5_RATE": _rate(t5_f, t5_n),
        "CURRENT_FILL5_ENRICHMENT": enr,
        "CURRENT_TOP1_N": t1_n,
        "CURRENT_TOP3_N": t3_n,
        "CURRENT_TOP5_N": t5_n,
        "daily": day_rows,
        "by_session": sess_rows,
    }


def _pair_spearman(rows: list[dict[str, Any]], ykey: str) -> Optional[float]:
    xs: list[float] = []
    ys: list[float] = []
    for r in rows:
        a = _f(r.get("current_score"))
        b = _f(r.get(ykey))
        if a is None or b is None:
            continue
        xs.append(float(a))
        ys.append(float(b))
    return spearman(xs, ys)


def current_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in labeled_rows(rows) if int(r.get("Y_FILL5") or 0) == 1]
    daily_u: list[float] = []
    daily_d: list[float] = []
    day_rows = []
    for day in ELIGIBLE_DAYS:
        xs = [r for r in filled if str(r.get("date") or "") == day]
        su = _pair_spearman(xs, "U_FILL")
        sd = _pair_spearman(xs, "D_FILL")
        if su is not None:
            daily_u.append(float(su))
        if sd is not None:
            daily_d.append(float(sd))
        day_rows.append({"date": day, "N": len(xs), "SPEARMAN_U": su, "SPEARMAN_D": sd})
    sess = {}
    for s in ("AM", "PM"):
        xs = [r for r in filled if session_of(r) == s]
        sess[s] = {
            "N": len(xs),
            "SPEARMAN_U": _pair_spearman(xs, "U_FILL"),
            "SPEARMAN_D": _pair_spearman(xs, "D_FILL"),
        }
    return {
        "N": len(filled),
        "CURRENT_SCORE_U_FILL_SPEARMAN": _pair_spearman(filled, "U_FILL"),
        "CURRENT_SCORE_D_FILL_SPEARMAN": _pair_spearman(filled, "D_FILL"),
        "DAILY_MEDIAN_SPEARMAN_U": _median(daily_u),
        "DAILY_MEDIAN_SPEARMAN_D": _median(daily_d),
        "daily": day_rows,
        "by_session": sess,
    }


def delay_effect(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in labeled_rows(rows) if int(r.get("Y_FILL5") or 0) == 1]
    buckets = {"0-1s": [], "1-2s": [], "2-5s": [], "overflow": []}
    delay_u: list[tuple[float, float]] = []
    delay_d: list[tuple[float, float]] = []
    for r in filled:
        ttf = _f(r.get("TIME_TO_FILL_SEC"))
        if ttf is None:
            buckets["overflow"].append(r)
            continue
        if ttf <= 1.0 + 1e-12:
            buckets["0-1s"].append(r)
        elif ttf <= 2.0 + 1e-12:
            buckets["1-2s"].append(r)
        elif ttf <= 5.0 + 1e-12:
            buckets["2-5s"].append(r)
        else:
            buckets["overflow"].append(r)
        u = _f(r.get("U_FILL"))
        d = _f(r.get("D_FILL"))
        if u is not None:
            delay_u.append((float(ttf), float(u)))
        if d is not None:
            delay_d.append((float(ttf), float(d)))
    recs = []
    for name in ("0-1s", "1-2s", "2-5s"):
        grp = buckets[name]
        uu = [float(v) for r in grp if (v := _f(r.get("U_FILL"))) is not None]
        dd = [float(v) for r in grp if (v := _f(r.get("D_FILL"))) is not None]
        recs.append(
            {
                "bucket": name,
                "N": len(grp),
                "U_FILL_MEAN": _mean(uu),
                "U_FILL_MEDIAN": _median(uu),
                "D_FILL_MEAN": _mean(dd),
                "D_FILL_MEDIAN": _median(dd),
            }
        )
    su = spearman([p[0] for p in delay_u], [p[1] for p in delay_u]) if delay_u else None
    sd = spearman([p[0] for p in delay_d], [p[1] for p in delay_d]) if delay_d else None
    return {
        "buckets": recs,
        "OVERFLOW_N": len(buckets["overflow"]),
        "FILL_DELAY_U_SPEARMAN": su,
        "FILL_DELAY_D_SPEARMAN": sd,
    }


def decide(
    *,
    parity_ok: bool,
    geom: dict[str, Any],
    sess: dict[str, Any],
    pareto: dict[str, Any],
    cur_fill: dict[str, Any],
    cur_q: dict[str, Any],
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "CASE": None,
            "VERDICT": "WAIT5_EXECUTION_REBASE_INTEGRITY_FAILED",
            "TARGET_ARCHITECTURE": None,
            "PRIMARY_MECHANISM": None,
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": "Frozen W5 harvest control did not reproduce.",
            "note": "STOP. BASE_PARITY failed.",
        }
    any_r = _f(geom.get("FILLABLE_ANY_COHORT_RATE"))
    multi_r = _f(geom.get("MULTI_FILLABLE_COHORT_RATE"))
    am_m = _f((sess.get("AM") or {}).get("MULTI_FILLABLE_COHORT_RATE"))
    pm_m = _f((sess.get("PM") or {}).get("MULTI_FILLABLE_COHORT_RATE"))
    dom = _f(pareto.get("MULTI_FILLABLE_WITH_DOMINANCE_RATE"))
    three_r = threeplus_rate(geom)
    overall_ok = bool(_ge(any_r, RATE_MIN) and _ge(multi_r, RATE_MIN))
    sess_ok = bool(_ge(am_m, RATE_MIN) and _ge(pm_m, RATE_MIN))
    dom_ok = bool(_ge(dom, DOMINANCE_MIN_RATE))
    if not overall_ok or (overall_ok and sess_ok and not dom_ok):
        return {
            "CASE": "C",
            "VERDICT": "WAIT5_EXECUTION_AWARE_RANKING_NOT_SUPPORTED",
            "TARGET_ARCHITECTURE": "NONE",
            "PRIMARY_MECHANISM": "WAIT5_RANKING_SUPPORT_INSUFFICIENT",
            "NEXT_RESEARCH": "EXECUTION_POLICY_ARCHITECTURE_REASSESSMENT",
            "PRIMARY_FINDING": (
                "Under W5, overall multi-fillable rate or fillable quality variation is insufficient "
                "for an execution-aware ranking target."
            ),
            "note": "CASE C. STOP. Architecture reassessment not started this run.",
        }
    if overall_ok and not sess_ok:
        return {
            "CASE": "B",
            "VERDICT": "WAIT5_SELECTION_CAPACITY_SESSION_ASYMMETRIC",
            "TARGET_ARCHITECTURE": "SESSION_SPLIT_ARCHITECTURE_REQUIRED",
            "PRIMARY_MECHANISM": "PM_MULTI_CAPACITY_BELOW_BAR",
            "NEXT_RESEARCH": "WAIT5_SESSION_ARCHITECTURE_PRECOMMIT",
            "PRIMARY_FINDING": (
                "W5 restores overall ANY and MULTI above 0.50, but AM/PM selection capacity is not shared. "
                "PM MULTI stays below 0.50, so a common execution-aware ranking architecture is not appropriate. "
                "No AM/PM models are trained this run."
            ),
            "note": "CASE B. STOP. Session-split architecture not precommitted this run.",
        }
    enr = _f(cur_fill.get("CURRENT_FILL5_ENRICHMENT"))
    su = _f(cur_q.get("CURRENT_SCORE_U_FILL_SPEARMAN"))
    sd = _f(cur_q.get("CURRENT_SCORE_D_FILL_SPEARMAN"))
    weak_q = bool(
        su is not None
        and sd is not None
        and abs(float(su)) < float(QUALITY_SPEARMAN_WEAK_MAX)
        and abs(float(sd)) < float(QUALITY_SPEARMAN_WEAK_MAX)
    )
    limited_multi = bool(three_r is not None and float(three_r) < float(RATE_MIN))
    if enr is not None and float(enr) > 1.0 and weak_q and limited_multi:
        return {
            "CASE": "D",
            "VERDICT": "WAIT5_FILLABILITY_TARGET_ONLY_SUPPORTED",
            "TARGET_ARCHITECTURE": "FILLABILITY_ONLY",
            "PRIMARY_MECHANISM": "CURRENT_ENRICHES_FILL_NOT_QUALITY",
            "NEXT_RESEARCH": "WAIT5_FILLABILITY_TARGET_LEARNABILITY",
            "PRIMARY_FINDING": (
                "CURRENT score concentrates W5 fillability but does not rank post-fill U/D, "
                "and full Top3 fillable capacity remains limited."
            ),
            "note": "CASE D. STOP. Fillability-target model not trained this run.",
        }
    return {
        "CASE": "A",
        "VERDICT": "WAIT5_EXECUTION_AWARE_REBASE_READY",
        "TARGET_ARCHITECTURE": "TWO_STAGE_EXECUTION_QUALITY",
        "PRIMARY_MECHANISM": "WAIT5_SUPPORTS_TWO_STAGE_RANKING",
        "NEXT_RESEARCH": "WAIT5_TWO_STAGE_TARGET_LEARNABILITY",
        "PRIMARY_FINDING": (
            "W5 provides overall and session multi-fillable capacity plus post-fill quality dominance. "
            "A two-stage fillability-then-quality target is the development architecture. "
            "No model is trained this run. Runtime WAIT remains 1.0."
        ),
        "note": "CASE A. STOP. Two-stage target not trained this run.",
    }

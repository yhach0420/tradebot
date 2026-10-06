"""T1/T2 existing Ridge OOF scores. Predicted Pareto diagnostic. No selection rule."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, session_of
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import (
    TARGET,
    executable_rows,
    fit_ridge_prepared,
    normalize_rows,
    ranking_pop,
    score_prepared,
    spearman,
    spec_label,
)
from research.multiobjective_feasibility import ELIGIBLE_DAYS, TOPK
from research.multiobjective_feasibility.analyze import cohort_key, mean_ud, pareto_mask, row_key


def _label(rows: list[dict[str, Any]], ykey: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec[TARGET] = rec.get(ykey)
        out.append(rec)
    return out


def _lodo_score(prepared: list[dict[str, Any]], spec: dict[str, Any], days: list[str], ykey: str, key: str) -> list[dict[str, Any]]:
    labeled = _label(prepared, ykey)
    scored: list[dict[str, Any]] = []
    for hold in days:
        tr = [r for r in labeled if str(r.get("date")) != hold]
        te = [r for r in labeled if str(r.get("date")) == hold]
        if not ranking_pop(tr) or not te:
            continue
        fit = fit_ridge_prepared(ranking_pop(tr), spec)
        scored.extend(score_prepared(te, fit, key=key))
    return scored


def process_fixed_spec(payload: dict[str, Any]) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    sid = spec_label(spec)
    rows_path = Path(payload["rows_path"])
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    print(f"  pred-spec start {sid}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    t1s = _lodo_score(prepared, spec, days, "T1", "t1_score")
    t2s = _lodo_score(prepared, spec, days, "T2", "t2_score")
    t2_by = {row_key(r): r for r in t2s}
    merged = []
    for r in t1s:
        o = t2_by.get(row_key(r))
        if o is None:
            continue
        rec = dict(r)
        rec["t2_score"] = o.get("t2_score")
        if _f(rec.get("t1_score")) is None or _f(rec.get("t2_score")) is None:
            continue
        if _f(rec.get("current_score")) is None:
            continue
        if _f(rec.get("T1")) is None or _f(rec.get("T2")) is None:
            continue
        if feats and not spec_features_ok(rec, feats):
            continue
        merged.append(rec)

    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in merged:
        by[cohort_key(r)].append(r)

    cohorts = []
    daily_mfe: dict[str, list[float]] = defaultdict(list)
    daily_dn: dict[str, list[float]] = defaultdict(list)
    spears = []
    overlaps = []
    psize = []
    pshare = []
    for key, grp in sorted(by.items()):
        n = len(grp)
        if n < int(MIN_COHORT_N):
            continue
        date, sess, an = key
        t1p = [float(r["t1_score"]) for r in grp]
        t2p = [float(r["t2_score"]) for r in grp]
        sp = spearman(t1p, t2p)
        if sp is not None:
            spears.append(float(sp))
        t1_top = rank_group(grp, "t1_score")[:TOPK]
        t2_top = rank_group(grp, "t2_score")[:TOPK]
        s1 = {str(r.get("symbol") or "") for r in t1_top}
        s2 = {str(r.get("symbol") or "") for r in t2_top}
        ov = len(s1 & s2)
        denom = max(min(len(s1), len(s2), TOPK), 1)
        ov_rate = ov / float(denom)
        overlaps.append(ov_rate)
        u = [float(r["T1"]) for r in grp]
        d = [float(r["T2"]) for r in grp]
        front = pareto_mask(t1p, t2p)
        front_n = int(sum(1 for v in front if v))
        psize.append(front_n)
        pshare.append(front_n / float(n))
        pareto_rows = [r for r, f in zip(grp, front) if f]
        non_rows = [r for r, f in zip(grp, front) if not f]
        cur_top = rank_group(grp, "current_score")[:TOPK]
        pu, pd_ = mean_ud(pareto_rows)
        nu, nd_ = mean_ud(non_rows)
        cu, cd_ = mean_ud(cur_top)
        mfe_d = (pu - cu) if pu is not None and cu is not None else None
        dn_d = (pd_ - cd_) if pd_ is not None and cd_ is not None else None
        if mfe_d is not None:
            daily_mfe[date].append(float(mfe_d))
        if dn_d is not None:
            daily_dn[date].append(float(dn_d))
        cohorts.append(
            {
                "date": date,
                "session": sess,
                "anchor": an,
                "n": n,
                "PREDICTED_T1_T2_SPEARMAN": sp,
                "TOP3_OVERLAP_N": ov,
                "TOP3_OVERLAP_RATE": ov_rate,
                "PREDICTED_PARETO_SIZE": front_n,
                "PREDICTED_PARETO_SHARE": front_n / float(n),
                "PARETO_MEAN_MFE": pu,
                "PARETO_MEAN_DOWNSIDE": pd_,
                "NONPARETO_MEAN_MFE": nu,
                "NONPARETO_MEAN_DOWNSIDE": nd_,
                "CURRENT_TOP3_MEAN_MFE": cu,
                "CURRENT_TOP3_MEAN_DOWNSIDE": cd_,
                "PARETO_MFE_DELTA_VS_CURRENT": mfe_d,
                "PARETO_DOWNSIDE_DELTA_VS_CURRENT": dn_d,
            }
        )

    def _day_mean(mp: dict[str, list[float]]) -> list[dict[str, Any]]:
        out = []
        for d in days:
            xs = mp.get(d) or []
            if not xs:
                continue
            out.append({"date": d, "value": float(np.mean(xs))})
        return out

    mfe_days = _day_mean(daily_mfe)
    dn_days = _day_mean(daily_dn)
    print(
        f"  pred-spec done {sid} cohorts={len(cohorts)} "
        f"spearman={float(np.median(spears)) if spears else None} "
        f"overlap={float(np.median(overlaps)) if overlaps else None}",
        flush=True,
    )
    return {
        "ok": True,
        "spec_id": sid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "alpha": spec.get("alpha"),
        "n_features": spec.get("n_features"),
        "MATCHED_ROWS": len(merged),
        "n_cohorts": len(cohorts),
        "PREDICTED_T1_T2_SPEARMAN_MEDIAN": float(np.median(spears)) if spears else None,
        "PREDICTED_T1_T2_SPEARMAN_MEAN": float(np.mean(spears)) if spears else None,
        "PREDICTED_T1_T2_SPEARMAN_MIN": float(np.min(spears)) if spears else None,
        "PREDICTED_T1_T2_SPEARMAN_MAX": float(np.max(spears)) if spears else None,
        "TOP3_OVERLAP_RATE_MEDIAN": float(np.median(overlaps)) if overlaps else None,
        "TOP3_OVERLAP_RATE_MEAN": float(np.mean(overlaps)) if overlaps else None,
        "PREDICTED_PARETO_SIZE_MEAN": float(np.mean(psize)) if psize else None,
        "PREDICTED_PARETO_SHARE_MEAN": float(np.mean(pshare)) if pshare else None,
        "daily_mfe_delta": mfe_days,
        "daily_downside_delta": dn_days,
        "cohorts": cohorts,
    }

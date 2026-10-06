"""Ridge-only nested OOF. Primary = mean daily Top3 uplift on executable-at-t0 cohorts."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from research.entry_objective_redesign_c3 import (
    FEATURE_SETS,
    MIN_COHORT_N,
    NORMS,
    RIDGE_ALPHAS,
)
from research.uniform10_entry_rebuild_v2.eligibility import rebase

TARGET = "EXECUTABLE_FORWARD_MID_RETURN_600S"
SCORE_KEY = "c3_score"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _rank(x: np.ndarray) -> np.ndarray:
    o = np.argsort(x, kind="mergesort")
    r = np.empty(x.size, dtype=float)
    r[o] = np.arange(x.size, dtype=float)
    return r


def ranking_pop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if r.get("modeling_bucket") != "PRIMARY_MODELING_ELIGIBLE":
            continue
        if _f(r.get(TARGET)) is None:
            continue
        out.append(r)
    return out


def eligibility_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reb = rebase(rows)
    target_n = int(reb.get("PRIMARY_MODELING_ELIGIBLE") or 0)
    exec_n = 0
    for r in rows:
        if r.get("modeling_bucket") != "PRIMARY_MODELING_ELIGIBLE":
            continue
        if _f(r.get(TARGET)) is None:
            continue
        if r.get("executable_at_t0"):
            exec_n += 1
    cohort_n: dict[str, int] = {}
    for r in ranking_pop(rows):
        key = f"{r.get('date')}|{r.get('anchor')}"
        cohort_n[key] = cohort_n.get(key, 0) + 1
    ns = list(cohort_n.values())
    return {
        **reb,
        "TOTAL_TARGET_V4_ROWS": target_n,
        "EXECUTABLE_T0_ROWS": exec_n,
        "NONEXEC_REMOVED_ROWS": target_n - exec_n,
        "RANKING_POP_ROWS": exec_n,
        "n_cohorts": len(ns),
        "cohort_eligible_n_mean": float(np.mean(ns)) if ns else None,
        "cohort_eligible_n_min": int(min(ns)) if ns else None,
        "cohort_eligible_n_max": int(max(ns)) if ns else None,
        "cohorts_lt_10": sum(1 for n in ns if n < int(MIN_COHORT_N)),
    }


def cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    return by


def apply_norm(cohort: list[dict[str, Any]], feats: list[str], mode: str) -> list[dict[str, Any]]:
    out = [dict(r) for r in cohort]
    if mode == "none" or not feats:
        return out
    for f in feats:
        xs = [_f(r.get(f)) for r in out]
        fin_idx = [i for i, v in enumerate(xs) if v is not None]
        if len(fin_idx) < 3:
            continue
        if mode == "cross_sectional_z":
            fin = [xs[i] for i in fin_idx]
            mu = float(np.mean(fin))
            sd = float(np.std(fin))
            for i in fin_idx:
                out[i][f] = 0.0 if sd <= 1e-12 else (xs[i] - mu) / sd
        elif mode == "robust_cross_sectional_rank":
            order = sorted(fin_idx, key=lambda i: (xs[i], str(out[i].get("symbol") or "")))
            for rank, i in enumerate(order):
                out[i][f] = (rank + 0.5) / max(len(order), 1)
    return out


def normalize_rows(rows: list[dict[str, Any]], feats: list[str], mode: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for grp in cohorts(rows).values():
        out.extend(apply_norm(grp, feats, mode))
    return out


def _matrix(rows: list[dict[str, Any]], feats: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X = []
    y = []
    ok = []
    for r in rows:
        vec = []
        good = True
        for f in feats:
            v = _f(r.get(f))
            if v is None:
                good = False
                break
            vec.append(float(v))
        yt = _f(r.get(TARGET))
        if yt is None:
            good = False
        ok.append(good)
        X.append(vec if good else [0.0] * len(feats))
        y.append(float(yt) if yt is not None else 0.0)
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), np.asarray(ok, dtype=bool)


def fit_ridge_prepared(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    X, y, ok = _matrix(train, feats)
    if int(ok.sum()) < 40:
        return {
            "feature_set": spec.get("feature_set"),
            "features": feats,
            "normalization": norm,
            "alpha": spec.get("alpha"),
            "kind": "fail",
        }
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[ok])
    m = Ridge(alpha=float(spec.get("alpha") or 1.0))
    m.fit(Xs, y[ok])
    return {
        "feature_set": spec.get("feature_set"),
        "features": feats,
        "normalization": norm,
        "alpha": spec.get("alpha"),
        "kind": "ridge",
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": m.coef_.tolist(),
        "intercept": float(m.intercept_),
        "train_n": int(ok.sum()),
    }


def fit_ridge(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    return fit_ridge_prepared(normalize_rows(train, feats, norm), spec)


def score_prepared(rows: list[dict[str, Any]], fit: dict[str, Any], *, key: str = SCORE_KEY) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        rec = dict(r)
        rec[key] = _predict_row(fit, rec) if fit.get("kind") == "ridge" else None
        out.append(rec)
    return out


def _predict_row(fit: dict[str, Any], row: dict[str, Any]) -> Optional[float]:
    if fit.get("kind") != "ridge":
        return None
    feats = list(fit.get("features") or [])
    vec = []
    for f in feats:
        v = _f(row.get(f))
        if v is None:
            return None
        vec.append(float(v))
    mean = np.asarray(fit.get("scaler_mean"), dtype=float)
    scale = np.asarray(fit.get("scaler_scale"), dtype=float)
    x = (np.asarray(vec, dtype=float) - mean) / np.where(scale == 0, 1.0, scale)
    return float(np.dot(fit["coef"], x) + float(fit["intercept"]))


def score_rows(rows: list[dict[str, Any]], fit: dict[str, Any], *, key: str = SCORE_KEY) -> list[dict[str, Any]]:
    feats = list(fit.get("features") or [])
    norm = str(fit.get("normalization") or "none")
    out = []
    for coh in cohorts(rows).values():
        normed = apply_norm(coh, feats, norm) if fit.get("kind") == "ridge" else [dict(r) for r in coh]
        for r in normed:
            rec = dict(r)
            rec[key] = _predict_row(fit, rec) if fit.get("kind") == "ridge" else None
            out.append(rec)
    return out


def score_lookup_rows(rows: list[dict[str, Any]], fit: dict[str, Any]) -> dict[str, float]:
    """Score executable-at-t0 names for Exact lookup. Target not required."""
    feats = list(fit.get("features") or [])
    norm = str(fit.get("normalization") or "none")
    lookup: dict[str, float] = {}
    exe = [r for r in rows if r.get("executable_at_t0")]
    for coh in cohorts(exe).values():
        normed = apply_norm(coh, feats, norm)
        for r in normed:
            s = _predict_row(fit, r)
            if s is None:
                continue
            lookup[f"{r.get('date')}|{r.get('anchor')}|{r.get('symbol')}"] = float(s)
    return lookup


def spearman(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < int(MIN_COHORT_N):
        return None
    aa = np.asarray(a, dtype=float)
    bb = np.asarray(b, dtype=float)
    if float(np.std(aa)) <= 1e-12 or float(np.std(bb)) <= 1e-12:
        return 0.0
    return float(np.corrcoef(_rank(aa), _rank(bb))[0, 1])


def _cohort_block(grp: list[dict[str, Any]], score_key: str) -> Optional[dict[str, Any]]:
    n_all = len(grp)
    if n_all < int(MIN_COHORT_N):
        return None
    ys = [_f(r.get(TARGET)) for r in grp]
    if any(v is None for v in ys):
        return None
    all_m = float(np.mean(ys))
    scored = [
        (_f(r.get(score_key)), _f(r.get(TARGET)), str(r.get("symbol") or ""))
        for r in grp
        if _f(r.get(score_key)) is not None and _f(r.get(TARGET)) is not None
    ]
    if len(scored) < int(MIN_COHORT_N):
        return None
    scored.sort(key=lambda z: (-z[0], z[2]))
    sp = spearman([p[0] for p in scored], [p[1] for p in scored])

    def topk(k: int) -> Optional[float]:
        if len(scored) < k:
            return None
        return float(np.mean([p[1] for p in scored[:k]]))

    t1, t3, t5, t10 = topk(1), topk(3), topk(5), topk(10)
    return {
        "n": n_all,
        "n_scored": len(scored),
        "ALL_TARGET": all_m,
        "TOP1_TARGET": t1,
        "TOP3_TARGET": t3,
        "TOP5_TARGET": t5,
        "TOP10_TARGET": t10,
        "TOP1_UPLIFT": (t1 - all_m) if t1 is not None else None,
        "TOP3_UPLIFT": (t3 - all_m) if t3 is not None else None,
        "TOP5_UPLIFT": (t5 - all_m) if t5 is not None else None,
        "TOP10_UPLIFT": (t10 - all_m) if t10 is not None else None,
        "spearman": sp,
    }


def ranking_metrics(rows: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    xs = ranking_pop(rows)
    by = cohorts(xs)
    n_cohorts_total = len(by)
    n_excluded = 0
    per_cohort: list[dict[str, Any]] = []
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (day, an), grp in sorted(by.items()):
        blk = _cohort_block(grp, score_key)
        if blk is None:
            n_excluded += 1
            continue
        rec = {"date": day, "anchor": an, **blk}
        per_cohort.append(rec)
        by_day[day].append(rec)

    def daily_mean(field: str) -> tuple[Optional[float], list[dict[str, Any]], int]:
        days = []
        for d, xs_d in sorted(by_day.items()):
            vs = [x[field] for x in xs_d if x.get(field) is not None]
            if not vs:
                continue
            days.append({"date": d, field: float(np.mean(vs)), "n_cohorts": len(vs)})
        vals = [x[field] for x in days]
        return (float(np.mean(vals)) if vals else None, days, len(vals))

    top1_m, top1_days, _ = daily_mean("TOP1_UPLIFT")
    top3_m, top3_days, _ = daily_mean("TOP3_UPLIFT")
    top5_m, top5_days, _ = daily_mean("TOP5_UPLIFT")
    top10_m, top10_days, _ = daily_mean("TOP10_UPLIFT")
    t1t, t1td, _ = daily_mean("TOP1_TARGET")
    t3t, _, _ = daily_mean("TOP3_TARGET")
    t5t, _, _ = daily_mean("TOP5_TARGET")
    t10t, _, _ = daily_mean("TOP10_TARGET")
    allt, _, _ = daily_mean("ALL_TARGET")
    sp_m, sp_days, _ = daily_mean("spearman")
    top3_vals = [x["TOP3_UPLIFT"] for x in top3_days]
    return {
        "n_cohorts_total": n_cohorts_total,
        "n_cohorts_used": len(per_cohort),
        "n_cohorts_excluded_lt10": n_excluded,
        "n_days": len(top3_days),
        "ALL_TARGET": allt,
        "TOP1_TARGET": t1t,
        "TOP3_TARGET": t3t,
        "TOP5_TARGET": t5t,
        "TOP10_TARGET": t10t,
        "TOP1_UPLIFT": top1_m,
        "TOP3_UPLIFT": top3_m,
        "TOP5_UPLIFT": top5_m,
        "TOP10_UPLIFT": top10_m,
        "MEAN_DAILY_SPEARMAN": sp_m,
        "TOP3_POSITIVE_DAY_COUNT": sum(1 for v in top3_vals if v is not None and v > 0),
        "TOP3_NEGATIVE_DAY_COUNT": sum(1 for v in top3_vals if v is not None and v < 0),
        "daily_top3": top3_days,
        "daily_top1": top1_days,
        "daily_top5": top5_days,
        "daily_top10": top10_days,
        "daily_spearman": sp_days,
        "daily_all": [{"date": d, "ALL_TARGET": float(np.mean([x["ALL_TARGET"] for x in xs_d]))} for d, xs_d in sorted(by_day.items())],
    }


def spec_grid() -> list[dict[str, Any]]:
    out = []
    for fs_name, feats in FEATURE_SETS.items():
        for norm in NORMS:
            for a in RIDGE_ALPHAS:
                out.append(
                    {
                        "feature_set": fs_name,
                        "features": list(feats),
                        "n_features": len(feats),
                        "normalization": norm,
                        "alpha": float(a),
                    }
                )
    return out


def spec_label(spec: dict[str, Any]) -> str:
    return f"{spec.get('feature_set')}|{spec.get('normalization')}|a{spec.get('alpha')}"


def _tie_key(row: dict[str, Any]) -> tuple:
    t3 = row.get("mean_daily_top3_uplift")
    t1 = row.get("mean_daily_top1_uplift")
    t5 = row.get("mean_daily_top5_uplift")
    mn = None
    if t1 is not None and t5 is not None:
        mn = min(float(t1), float(t5))
    sp = row.get("mean_daily_spearman")
    nfeat = int(row.get("n_features") or 99)
    alpha = float(row.get("alpha") or 0.0)
    return (
        -(float(t3) if t3 is not None else -1e18),
        -(float(mn) if mn is not None else -1e18),
        -(float(sp) if sp is not None else -1e18),
        nfeat,
        -alpha,
    )


def executable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("executable_at_t0")]


def fit_on_universe(train_all: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(train_all), feats, norm)
    return fit_ridge_prepared(ranking_pop(prepared), spec)


def eval_spec_cv(rows: list[dict[str, Any]], spec: dict[str, Any], days: list[str]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    top3s: list[float] = []
    top1s: list[float] = []
    top5s: list[float] = []
    sps: list[float] = []
    for hold in days:
        tr = [r for r in prepared if str(r.get("date")) != hold]
        te = [r for r in prepared if str(r.get("date")) == hold]
        if not ranking_pop(tr) or not ranking_pop(te):
            continue
        fit = fit_ridge_prepared(ranking_pop(tr), spec)
        scored = score_prepared(te, fit)
        m = ranking_metrics(scored, SCORE_KEY)
        if m.get("TOP3_UPLIFT") is not None:
            top3s.append(float(m["TOP3_UPLIFT"]))
        if m.get("TOP1_UPLIFT") is not None:
            top1s.append(float(m["TOP1_UPLIFT"]))
        if m.get("TOP5_UPLIFT") is not None:
            top5s.append(float(m["TOP5_UPLIFT"]))
        if m.get("MEAN_DAILY_SPEARMAN") is not None:
            sps.append(float(m["MEAN_DAILY_SPEARMAN"]))
    return {
        **spec,
        "spec_id": spec_label(spec),
        "mean_daily_top3_uplift": float(np.mean(top3s)) if top3s else None,
        "mean_daily_top1_uplift": float(np.mean(top1s)) if top1s else None,
        "mean_daily_top5_uplift": float(np.mean(top5s)) if top5s else None,
        "mean_daily_spearman": float(np.mean(sps)) if sps else None,
        "inner_n_days": len(top3s),
    }


def inner_select(train: list[dict[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("date")) for r in ranking_pop(train)})
    grid = spec_grid()
    ranked = []
    for i, spec in enumerate(grid, start=1):
        rec = eval_spec_cv(train, spec, days)
        ranked.append(rec)
        print(
            f"    inner {i}/{len(grid)} {rec['spec_id']} "
            f"top3={rec.get('mean_daily_top3_uplift')} n={rec.get('inner_n_days')}",
            flush=True,
        )
    ranked.sort(key=_tie_key)
    return {"inner_leaderboard": ranked, "selected": ranked[0] if ranked else {}}


def nested_oof(rows: list[dict[str, Any]], *, fold_cache_dir: Any = None) -> dict[str, Any]:
    pop = ranking_pop(rows)
    days = sorted({str(r.get("date")) for r in pop})
    oof_rows: list[dict[str, Any]] = []
    fold_recs = []
    selected_specs = []
    cache_dir = Path(fold_cache_dir) if fold_cache_dir else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    for hold in days:
        print(f"  outer holdout {hold}", flush=True)
        train = [r for r in rows if str(r.get("date")) != hold]
        test = [r for r in rows if str(r.get("date")) == hold]
        fold_fp = cache_dir / f"oof_fold_{hold}.json" if cache_dir is not None else None
        if fold_fp is not None and fold_fp.is_file():
            saved = json.loads(fold_fp.read_text(encoding="utf-8"))
            spec = saved.get("spec") or {}
            scored = saved.get("scored") or []
            fold_recs.append(saved.get("fold") or {})
            selected_specs.append(spec)
            oof_rows.extend(scored)
            print(f"    fold cache-hit {hold} spec={spec.get('spec_id')}", flush=True)
            continue
        inner = inner_select(train)
        spec = inner.get("selected") or {}
        selected_specs.append(spec)
        fit = fit_on_universe(train, spec)
        scored = score_rows(executable_rows(test), fit)
        oof_rows.extend(scored)
        hold_m = ranking_metrics(scored, SCORE_KEY)
        cur_m = ranking_metrics(test, "current_score")
        fold_rec = {
            "holdout_day": hold,
            "feature_set": spec.get("feature_set"),
            "normalization": spec.get("normalization"),
            "alpha": spec.get("alpha"),
            "n_features": spec.get("n_features"),
            "spec_id": spec.get("spec_id"),
            "inner_top3": spec.get("mean_daily_top3_uplift"),
            "holdout_c3_top3": hold_m.get("TOP3_UPLIFT"),
            "holdout_cur_top3": cur_m.get("TOP3_UPLIFT"),
            "holdout_c3_top1": hold_m.get("TOP1_UPLIFT"),
            "holdout_cur_top1": cur_m.get("TOP1_UPLIFT"),
            "holdout_c3_top5": hold_m.get("TOP5_UPLIFT"),
            "holdout_cur_top5": cur_m.get("TOP5_UPLIFT"),
            "holdout_c3_spearman": hold_m.get("MEAN_DAILY_SPEARMAN"),
            "holdout_cur_spearman": cur_m.get("MEAN_DAILY_SPEARMAN"),
            "inner_leaderboard_head": [
                {
                    "spec_id": x.get("spec_id"),
                    "mean_daily_top3_uplift": x.get("mean_daily_top3_uplift"),
                }
                for x in (inner.get("inner_leaderboard") or [])[:5]
            ],
        }
        fold_recs.append(fold_rec)
        if fold_fp is not None:
            slim_spec = {k: v for k, v in spec.items() if k != "features"}
            slim_spec["features"] = spec.get("features")
            fold_fp.write_text(
                json.dumps(
                    {"spec": slim_spec, "fold": fold_rec, "scored": slim_oof_rows(scored)},
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
    cur_all = ranking_metrics(pop, "current_score")
    c3_all = ranking_metrics(oof_rows, SCORE_KEY)
    keys = [spec_label(s) for s in selected_specs]
    counts = Counter(keys)
    most = counts.most_common(1)[0] if counts else ("", 0)
    return {
        "folds": fold_recs,
        "selected_specs": [{k: v for k, v in s.items() if k != "features"} | {"features": s.get("features")} for s in selected_specs],
        "oof_rows": oof_rows,
        "current": cur_all,
        "c3": c3_all,
        "SPEC_COUNTS": dict(counts),
        "MOST_COMMON_SPEC": most[0],
        "MOST_COMMON_SPEC_SHARE": (most[1] / len(keys)) if keys else None,
    }


def slim_oof_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = (
        "date",
        "symbol",
        "anchor",
        "session",
        "current_score",
        SCORE_KEY,
        TARGET,
        "executable_at_t0",
        "modeling_bucket",
    )
    out = []
    for r in rows:
        rec = {k: r.get(k) for k in keep}
        out.append(rec)
    return out

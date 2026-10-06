"""Session-isolated LODO fillability classifiers and AM U/D regressors. No selection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import average_precision_score, roc_auc_score

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, rank_group, session_of
from research.direct_joint_objective import ELIGIBLE_DAYS
from research.direct_joint_objective.oof import representation_grid
from research.entry_objective_redesign_c3 import F2_UNION, MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import normalize_rows, spearman
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import group_cohorts
from research.passive_wait_policy_reassessment.analyze import _rate
from research.wait5_session_target_learnability import (
    AM_TOPK,
    FORBIDDEN_FEATURES,
    PM_TOPK,
    RF_CLF_PARAMS,
    RF_REG_PARAMS,
)

SLIM_KEYS = (
    "date",
    "anchor",
    "symbol",
    "session",
    "current_score",
    "Y_FILL5",
    "U_FILL",
    "D_FILL",
    "executable_at_t0",
    "common_cohort",
    "joint_label",
    "future_event_use",
    *F2_UNION,
)


def slim_row(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in SLIM_KEYS:
        if k not in r:
            continue
        v = r.get(k)
        if k == "Y_FILL5" and v is not None:
            try:
                v = int(v)
            except (TypeError, ValueError):
                pass
        out[k] = v
    return out


def _feat_matrix(rows: list[dict[str, Any]], feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    X = []
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
        ok.append(good)
        X.append(vec if good else [0.0] * len(feats))
    return np.asarray(X, dtype=float), np.asarray(ok, dtype=bool)


def _pos_proba(model: Any, X: np.ndarray) -> np.ndarray:
    if X.size == 0:
        return np.zeros((0,), dtype=float)
    proba = model.predict_proba(X)
    classes = [int(c) for c in model.classes_]
    if 1 not in classes:
        return np.zeros(int(X.shape[0]), dtype=float)
    return np.asarray(proba[:, classes.index(1)], dtype=float)


def _clf_metrics(y: list[int], p: list[float]) -> tuple[Optional[float], Optional[float]]:
    if len(y) < 2 or len(y) != len(p):
        return None, None
    ap = float(average_precision_score(y, p))
    if len(set(y)) < 2:
        return None, ap
    return float(roc_auc_score(y, p)), ap


def admission_fill(
    rows: list[dict[str, Any]],
    score_key: str,
    k: int,
    days: list[str] | None = None,
) -> dict[str, Any]:
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    by = group_cohorts(rows)
    n_coh = n_sel = n_fill = n_any = 0
    fills_per: list[int] = []
    daily = {d: {"coh": 0, "sel": 0, "fill": 0, "any": 0} for d in use_days}
    for (date, _sess, _an), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, score_key)
        top = ranked[: int(k)]
        if not top:
            continue
        nf = sum(1 for r in top if int(r.get("Y_FILL5") or 0) == 1)
        n_coh += 1
        n_sel += len(top)
        n_fill += nf
        n_any += int(nf > 0)
        fills_per.append(int(nf))
        b = daily.get(str(date))
        if b is not None:
            b["coh"] += 1
            b["sel"] += len(top)
            b["fill"] += nf
            b["any"] += int(nf > 0)
    day_rows = []
    for d in use_days:
        b = daily[d]
        if int(b["coh"]) <= 0:
            continue
        day_rows.append(
            {
                "date": d,
                "FILL_RATE": _rate(int(b["fill"]), int(b["sel"])),
                "ANY_FILL_COHORT_RATE": _rate(int(b["any"]), int(b["coh"])),
                "COHORT_N": int(b["coh"]),
                "FILL_N": int(b["fill"]),
                "SELECTED_N": int(b["sel"]),
            }
        )
    return {
        "FILL_RATE": _rate(n_fill, n_sel),
        "ANY_FILL_COHORT_RATE": _rate(n_any, n_coh),
        "MEAN_FILLS_SELECTED_PER_COHORT": float(np.mean(fills_per)) if fills_per else None,
        "COHORT_N": n_coh,
        "SELECTED_N": n_sel,
        "FILL_N": n_fill,
        "daily": day_rows,
    }


def quality_eval(
    rows: list[dict[str, Any]],
    pred_key: str,
    ykey: str,
    days: list[str] | None = None,
) -> dict[str, Any]:
    """Rank fillable names inside AM multi-fillable cohorts only."""
    use_days = [str(d) for d in (days or ELIGIBLE_DAYS)]
    by = group_cohorts(rows)
    pred_all: list[float] = []
    y_all: list[float] = []
    upl_all: list[float] = []
    by_day_p: dict[str, list[float]] = defaultdict(list)
    by_day_y: dict[str, list[float]] = defaultdict(list)
    by_day_u: dict[str, list[float]] = defaultdict(list)
    n_coh = 0
    for (date, _sess, _an), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        filled = [r for r in grp if int(r.get("Y_FILL5") or 0) == 1 and _f(r.get(ykey)) is not None]
        if len(filled) < 2:
            continue
        complete = [r for r in filled if _f(r.get(pred_key)) is not None]
        if len(complete) < 2:
            continue
        n_coh += 1
        preds = [float(r[pred_key]) for r in complete]
        ys = [float(r[ykey]) for r in complete]
        pred_all.extend(preds)
        y_all.extend(ys)
        by_day_p[str(date)].extend(preds)
        by_day_y[str(date)].extend(ys)
        ranked = rank_group(complete, pred_key)
        top = ranked[0]
        mean_y = float(np.mean(ys))
        upl = float(top[ykey]) - mean_y
        upl_all.append(upl)
        by_day_u[str(date)].append(upl)
    daily = []
    daily_sp: list[float] = []
    daily_upl: list[float] = []
    for d in use_days:
        sp = spearman(by_day_p.get(d) or [], by_day_y.get(d) or [])
        xs = by_day_u.get(d) or []
        upl = float(np.mean(xs)) if xs else None
        if sp is not None:
            daily_sp.append(float(sp))
        if upl is not None:
            daily_upl.append(float(upl))
        if sp is None and upl is None:
            continue
        daily.append({"date": d, "SPEARMAN": sp, "TOP1_UPLIFT": upl, "N": len(by_day_p.get(d) or [])})
    sp_st = delta_series_stats(daily_sp) if daily_sp else {
        "mean": None,
        "median": None,
        "positive_days": 0,
        "negative_days": 0,
        "ex_best_day": None,
        "ex_top3_days": None,
        "n_days": 0,
    }
    return {
        "OVERALL_SPEARMAN": spearman(pred_all, y_all),
        "EVAL_N": len(pred_all),
        "EVAL_COHORT_N": n_coh,
        "TOP1_UPLIFT": float(np.mean(upl_all)) if upl_all else None,
        "MEDIAN_DAILY_SPEARMAN": float(np.median(daily_sp)) if daily_sp else None,
        "MEDIAN_DAILY_TOP1_UPLIFT": float(np.median(daily_upl)) if daily_upl else None,
        "POSITIVE_DAYS": sp_st.get("positive_days"),
        "NEGATIVE_DAYS": sp_st.get("negative_days"),
        "EX_BEST_DAY": sp_st.get("ex_best_day"),
        "EX_TOP3_DAYS": sp_st.get("ex_top3_days"),
        "DAILY_SPEARMAN_N": sp_st.get("n_days"),
        "daily": daily,
    }


def _train_ok(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("Y_FILL5") not in (0, 1):
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        out.append(r)
    return out


def _reg_pop(rows: list[dict[str, Any]], feats: list[str], ykey: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        if _f(r.get(ykey)) is None:
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        out.append(r)
    return out


def process_am_rep(payload: dict[str, Any]) -> dict[str, Any]:
    return process_rep(payload, session="AM", k=int(AM_TOPK), do_quality=True)


def process_pm_rep(payload: dict[str, Any]) -> dict[str, Any]:
    return process_rep(payload, session="PM", k=int(PM_TOPK), do_quality=False)


def process_rep(payload: dict[str, Any], *, session: str, k: int, do_quality: bool) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or payload.get("representation_id") or "")
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    print(f"  start {session} {rid}", flush=True)
    body = payload.get("rows")
    if body is None:
        from pathlib import Path
        import json

        raw = json.loads(Path(payload["rows_path"]).read_text(encoding="utf-8"))
        rows = list(raw.get("rows") or [])
    else:
        rows = list(body)

    leak = {
        "AM_TRAIN_PM_ROW_N": 0,
        "PM_TRAIN_AM_ROW_N": 0,
        "AM_NORMALIZER_PM_ROW_N": 0,
        "PM_NORMALIZER_AM_ROW_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
    }
    for f in feats:
        if str(f) in FORBIDDEN_FEATURES:
            leak["TARGET_CONTAMINATION_N"] += 1
    for r in rows:
        if r.get("future_event_use"):
            leak["FUTURE_EVENT_USE_N"] += 1
        sess = session_of(r)
        if session == "AM" and sess == "PM":
            leak["AM_NORMALIZER_PM_ROW_N"] += 1
        if session == "PM" and sess == "AM":
            leak["PM_NORMALIZER_AM_ROW_N"] += 1

    sess_rows = [r for r in rows if session_of(r) == session]
    prepared = normalize_rows(sess_rows, feats, norm)
    scored: list[dict[str, Any]] = []
    fold_n = 0
    stage2_u_n = []
    stage2_d_n = []
    for hold in days:
        tr_all = [r for r in prepared if str(r.get("date")) != hold]
        te = [r for r in prepared if str(r.get("date")) == hold]
        for r in tr_all:
            if str(r.get("date")) == hold:
                leak["HELDOUT_FIT_LEAK_N"] += 1
            sess = session_of(r)
            if session == "AM" and sess == "PM":
                leak["AM_TRAIN_PM_ROW_N"] += 1
            if session == "PM" and sess == "AM":
                leak["PM_TRAIN_AM_ROW_N"] += 1
        tr = _train_ok(tr_all, feats)
        if len(tr) < 40 or not te:
            for r in te:
                rec = dict(r)
                rec["fill_score"] = None
                rec["pred_U"] = None
                rec["pred_D"] = None
                scored.append(rec)
            continue
        X, ok = _feat_matrix(tr, feats)
        y = np.asarray([int(r.get("Y_FILL5") or 0) for r in tr], dtype=int)
        if int(ok.sum()) < 40:
            for r in te:
                rec = dict(r)
                rec["fill_score"] = None
                rec["pred_U"] = None
                rec["pred_D"] = None
                scored.append(rec)
            continue
        clf = RandomForestClassifier(**RF_CLF_PARAMS)
        clf.fit(X[ok], y[ok])
        Xt, tok = _feat_matrix(te, feats)
        proba = np.full(len(te), np.nan, dtype=float)
        if int(tok.sum()) > 0:
            proba[tok] = _pos_proba(clf, Xt[tok])

        pred_u = np.full(len(te), np.nan, dtype=float)
        pred_d = np.full(len(te), np.nan, dtype=float)
        if do_quality:
            tru = _reg_pop(tr_all, feats, "U_FILL")
            trd = _reg_pop(tr_all, feats, "D_FILL")
            stage2_u_n.append(len(tru))
            stage2_d_n.append(len(trd))
            if len(tru) >= 40:
                Xu, oku = _feat_matrix(tru, feats)
                yu = np.asarray([float(r["U_FILL"]) for r in tru], dtype=float)
                if int(oku.sum()) >= 40:
                    ru = RandomForestRegressor(**RF_REG_PARAMS)
                    ru.fit(Xu[oku], yu[oku])
                    if int(tok.sum()) > 0:
                        pred_u[tok] = np.asarray(ru.predict(Xt[tok]), dtype=float)
            if len(trd) >= 40:
                Xd, okd = _feat_matrix(trd, feats)
                yd = np.asarray([float(r["D_FILL"]) for r in trd], dtype=float)
                if int(okd.sum()) >= 40:
                    rd = RandomForestRegressor(**RF_REG_PARAMS)
                    rd.fit(Xd[okd], yd[okd])
                    if int(tok.sum()) > 0:
                        pred_d[tok] = np.asarray(rd.predict(Xt[tok]), dtype=float)

        for r, pr, pu, pd, good in zip(te, proba, pred_u, pred_d, tok):
            rec = dict(r)
            rec["fill_score"] = float(pr) if bool(good) and pr == pr else None
            rec["pred_U"] = float(pu) if bool(good) and pu == pu else None
            rec["pred_D"] = float(pd) if bool(good) and pd == pd else None
            scored.append(rec)
        fold_n += 1
        print(
            f"    {session} {rid} hold={hold} train={int(ok.sum())} pos={int(y[ok].sum())} te={len(te)}",
            flush=True,
        )

    fill = admission_fill(scored, "fill_score", k, days)
    y_true: list[int] = []
    y_score: list[float] = []
    for r in scored:
        sc = _f(r.get("fill_score"))
        if sc is None:
            continue
        y_true.append(int(r.get("Y_FILL5") or 0))
        y_score.append(float(sc))
    auc, ap = _clf_metrics(y_true, y_score)
    u_block = quality_eval(scored, "pred_U", "U_FILL", days) if do_quality else None
    d_block = quality_eval(scored, "pred_D", "D_FILL", days) if do_quality else None
    print(
        f"  done {session} {rid} folds={fold_n} fill={fill.get('FILL_RATE')} auc={auc} ap={ap}",
        flush=True,
    )
    return {
        "ok": True,
        "session": session,
        "representation_id": rid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "n_features": spec.get("n_features"),
        "outer_folds": fold_n,
        "MODEL_FILL_RATE": fill.get("FILL_RATE"),
        "ANY_FILL_COHORT_RATE": fill.get("ANY_FILL_COHORT_RATE"),
        "MEAN_FILLS_SELECTED_PER_COHORT": fill.get("MEAN_FILLS_SELECTED_PER_COHORT"),
        "COHORT_N": fill.get("COHORT_N"),
        "SELECTED_N": fill.get("SELECTED_N"),
        "FILL_N": fill.get("FILL_N"),
        "ROC_AUC": auc,
        "AVERAGE_PRECISION": ap,
        "OOF_SCORED_N": len(y_true),
        "daily_fill": fill.get("daily"),
        "u_quality": u_block,
        "d_quality": d_block,
        "STAGE2_U_TRAIN_MIN_N": min(stage2_u_n) if stage2_u_n else None,
        "STAGE2_D_TRAIN_MIN_N": min(stage2_d_n) if stage2_d_n else None,
        "integrity": leak,
    }


__all__ = [
    "admission_fill",
    "process_am_rep",
    "process_pm_rep",
    "quality_eval",
    "representation_grid",
    "slim_row",
]

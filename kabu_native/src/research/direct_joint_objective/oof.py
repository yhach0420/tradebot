"""9 frozen RF classifier representations. Direct joint-improvement label. No selection."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import executable_rows, normalize_rows
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import cohort_key, mean_ud, pareto_mask
from research.direct_joint_objective import ELIGIBLE_DAYS, RF_PARAMS, TOPK


def representation_grid() -> list[dict[str, Any]]:
    from research.entry_objective_redesign_c3 import FEATURE_SETS, NORMS

    out = []
    for fs_name, feats in FEATURE_SETS.items():
        for norm in NORMS:
            out.append(
                {
                    "representation_id": f"{fs_name}|{norm}",
                    "feature_set": fs_name,
                    "features": list(feats),
                    "n_features": len(feats),
                    "normalization": norm,
                }
            )
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


def _train_matrix(rows: list[dict[str, Any]], feats: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    X, ok_feat = _feat_matrix(rows, feats)
    y = []
    ok = []
    for r, feat_ok in zip(rows, ok_feat):
        lab = r.get("joint_label")
        good = bool(feat_ok) and lab in (0, 1)
        ok.append(good)
        y.append(int(lab) if lab in (0, 1) else 0)
    return X, np.asarray(y, dtype=int), np.asarray(ok, dtype=bool)


def _pos_proba(model: Any, X: np.ndarray) -> np.ndarray:
    if X.size == 0:
        return np.zeros((0,), dtype=float)
    proba = model.predict_proba(X)
    classes = [int(c) for c in model.classes_]
    if 1 not in classes:
        return np.zeros(int(X.shape[0]), dtype=float)
    return np.asarray(proba[:, classes.index(1)], dtype=float)


def attach_joint_labels(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """CURRENT Top3 first, then join U/D. No rank backfill. Future labels only."""
    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in rows:
        r.pop("joint_label", None)
        r.pop("CURRENT_U_BASELINE", None)
        r.pop("CURRENT_D_BASELINE", None)
        r["is_current_top3"] = False
        if not r.get("executable_at_t0"):
            continue
        if not r.get("common_cohort"):
            continue
        if _f(r.get("current_score")) is None:
            continue
        by[cohort_key(r)].append(r)

    pos = 0
    neg = 0
    n_coh = 0
    n_skip_incomplete_top3 = 0
    daily: dict[str, list[int]] = defaultdict(list)
    for (_date, _sess, _an), grp in by.items():
        if len(grp) < int(MIN_COHORT_N):
            continue
        ranked = rank_group(grp, "current_score")
        top = ranked[:TOPK]
        us = [_f(r.get("T1")) for r in top]
        ds = [_f(r.get("T2")) for r in top]
        if any(v is None for v in us) or any(v is None for v in ds) or len(top) < TOPK:
            n_skip_incomplete_top3 += 1
            continue
        cu = float(np.mean([float(v) for v in us]))
        cd = float(np.mean([float(v) for v in ds]))
        n_coh += 1
        for r in top:
            r["is_current_top3"] = True
        for r in grp:
            r["CURRENT_U_BASELINE"] = cu
            r["CURRENT_D_BASELINE"] = cd
            u = _f(r.get("T1"))
            d = _f(r.get("T2"))
            if u is None or d is None:
                r["joint_label"] = None
                continue
            lab = 1 if (float(u) > cu and float(d) > cd) else 0
            r["joint_label"] = lab
            if lab == 1:
                pos += 1
            else:
                neg += 1
            daily[str(r.get("date") or "")].append(lab)

    daily_rows = []
    for d in ELIGIBLE_DAYS:
        xs = daily.get(d) or []
        if not xs:
            continue
        p = int(sum(xs))
        daily_rows.append(
            {
                "date": d,
                "JOINT_LABEL_POSITIVE_N": p,
                "JOINT_LABEL_NEGATIVE_N": int(len(xs) - p),
                "JOINT_LABEL_POSITIVE_RATE": float(p / len(xs)),
                "LABELED_N": len(xs),
            }
        )
    tot = pos + neg
    return {
        "JOINT_LABEL_POSITIVE_N": pos,
        "JOINT_LABEL_NEGATIVE_N": neg,
        "JOINT_LABEL_POSITIVE_RATE": (pos / float(tot)) if tot else None,
        "LABELED_N": tot,
        "LABELED_COHORT_N": n_coh,
        "SKIP_INCOMPLETE_CURRENT_TOP3_N": n_skip_incomplete_top3,
        "daily_label_rate": daily_rows,
    }


def train_pop(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get("joint_label") not in (0, 1):
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        out.append(r)
    return out


def eval_pop(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if _f(r.get("current_score")) is None:
            continue
        if _f(r.get("joint_score")) is None:
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        out.append(r)
    return out


def _clf_metrics(y: list[int], p: list[float]) -> tuple[Optional[float], Optional[float]]:
    if len(y) < 2 or len(y) != len(p):
        return None, None
    ap = float(average_precision_score(y, p))
    if len(set(y)) < 2:
        return None, ap
    return float(roc_auc_score(y, p)), ap


def process_representation(payload: dict[str, Any]) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or "")
    rows_path = Path(payload["rows_path"])
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    print(f"  rf-clf start {rid}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    label_stats = attach_joint_labels(rows)
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    scored: list[dict[str, Any]] = []
    fold_n = 0
    for hold in days:
        tr = train_pop([r for r in prepared if str(r.get("date")) != hold], feats)
        te = [r for r in prepared if str(r.get("date")) == hold]
        if len(tr) < 40 or not te:
            continue
        X, y, ok = _train_matrix(tr, feats)
        if int(ok.sum()) < 40:
            continue
        model = RandomForestClassifier(**RF_PARAMS)
        model.fit(X[ok], y[ok])
        Xt, tok = _feat_matrix(te, feats)
        proba = np.full(len(te), np.nan, dtype=float)
        if int(tok.sum()) > 0:
            proba[tok] = _pos_proba(model, Xt[tok])
        for r, pr, good in zip(te, proba, tok):
            rec = dict(r)
            if not bool(good) or pr != pr:
                rec["joint_score"] = None
            else:
                rec["joint_score"] = float(pr)
            scored.append(rec)
        fold_n += 1
        print(
            f"    {rid} hold={hold} train={int(ok.sum())} pos={int(y[ok].sum())} te={len(te)}",
            flush=True,
        )

    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in eval_pop(scored, feats):
        by[cohort_key(r)].append(r)

    daily_mfe: dict[str, list[float]] = defaultdict(list)
    daily_dn: dict[str, list[float]] = defaultdict(list)
    daily_joint: dict[str, list[float]] = defaultdict(list)
    joint_flags = []
    member_n = 0
    top_n = 0
    dominated_n = 0
    n_coh = 0
    y_true: list[int] = []
    y_score: list[float] = []
    for (date, _sess, _an), grp in sorted(by.items()):
        if len(grp) < int(MIN_COHORT_N):
            continue
        for r in grp:
            lab = r.get("joint_label")
            sc = _f(r.get("joint_score"))
            if lab in (0, 1) and sc is not None:
                y_true.append(int(lab))
                y_score.append(float(sc))
        ranked = rank_group(grp, "joint_score")
        direct_top = ranked[:TOPK]
        du = [_f(r.get("T1")) for r in direct_top]
        dd = [_f(r.get("T2")) for r in direct_top]
        if any(v is None for v in du) or any(v is None for v in dd) or len(direct_top) < TOPK:
            continue
        cu = cd = None
        for r in grp:
            if _f(r.get("CURRENT_U_BASELINE")) is not None and _f(r.get("CURRENT_D_BASELINE")) is not None:
                cu = float(r["CURRENT_U_BASELINE"])
                cd = float(r["CURRENT_D_BASELINE"])
                break
        if cu is None or cd is None:
            continue
        n_coh += 1
        ru, rd = mean_ud(direct_top)
        if ru is not None and cu is not None:
            daily_mfe[date].append(float(ru - cu))
        if rd is not None and cd is not None:
            daily_dn[date].append(float(rd - cd))
        if ru is not None and cu is not None and rd is not None and cd is not None:
            hit = bool(ru > cu and rd > cd)
            joint_flags.append(hit)
            daily_joint[date].append(1.0 if hit else 0.0)
        labeled = [r for r in grp if _f(r.get("T1")) is not None and _f(r.get("T2")) is not None]
        if labeled:
            au = [float(r["T1"]) for r in labeled]
            ad = [float(r["T2"]) for r in labeled]
            front_act = pareto_mask(au, ad)
            idx = {id(r): i for i, r in enumerate(labeled)}
            for r in direct_top:
                i = idx.get(id(r))
                if i is None:
                    continue
                top_n += 1
                if front_act[i]:
                    member_n += 1
                else:
                    dominated_n += 1

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
    jt_days = _day_mean(daily_joint)
    mfe_st = delta_series_stats([float(r["value"]) for r in mfe_days])
    dn_st = delta_series_stats([float(r["value"]) for r in dn_days])
    auc, ap = _clf_metrics(y_true, y_score)
    print(
        f"  rf-clf done {rid} folds={fold_n} cohorts={n_coh} "
        f"mfe_d={mfe_st.get('mean')} dn_d={dn_st.get('mean')} "
        f"joint_rate={(sum(joint_flags) / len(joint_flags)) if joint_flags else None} "
        f"auc={auc} ap={ap}",
        flush=True,
    )
    return {
        "ok": True,
        "representation_id": rid,
        "feature_set": spec.get("feature_set"),
        "normalization": spec.get("normalization"),
        "n_features": spec.get("n_features"),
        "outer_folds": fold_n,
        "n_cohorts": n_coh,
        "TOP3_MFE_DELTA": mfe_st.get("mean"),
        "TOP3_DOWNSIDE_DELTA": dn_st.get("mean"),
        "JOINT_COHORT_SUCCESS_RATE": (sum(joint_flags) / float(len(joint_flags))) if joint_flags else None,
        "MFE_POSITIVE_DAYS": mfe_st.get("positive_days"),
        "MFE_NEGATIVE_DAYS": mfe_st.get("negative_days"),
        "MFE_EX_BEST_DAY": mfe_st.get("ex_best_day"),
        "MFE_EX_TOP3_DAYS": mfe_st.get("ex_top3_days"),
        "DOWNSIDE_POSITIVE_DAYS": dn_st.get("positive_days"),
        "DOWNSIDE_NEGATIVE_DAYS": dn_st.get("negative_days"),
        "DOWNSIDE_EX_BEST_DAY": dn_st.get("ex_best_day"),
        "DOWNSIDE_EX_TOP3_DAYS": dn_st.get("ex_top3_days"),
        "ROC_AUC": auc,
        "AVERAGE_PRECISION": ap,
        "OOF_LABELED_N": len(y_true),
        "ACTUAL_PARETO_MEMBER_RATE": (member_n / float(top_n)) if top_n else None,
        "ACTUAL_DOMINATED_RATE": (dominated_n / float(top_n)) if top_n else None,
        "daily_mfe": mfe_days,
        "daily_downside": dn_days,
        "daily_joint": jt_days,
        **label_stats,
    }

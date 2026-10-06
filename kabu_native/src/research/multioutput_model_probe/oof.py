"""9 frozen RF representations. Multi-output [U,D]. Maximin joint score. No selection."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import rankdata
from sklearn.ensemble import RandomForestRegressor

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import executable_rows, normalize_rows, spearman
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import cohort_key, mean_ud, pareto_mask
from research.multioutput_model_probe import ELIGIBLE_DAYS, RF_PARAMS, TOPK


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
        u = _f(r.get("T1"))
        d = _f(r.get("T2"))
        if u is None or d is None:
            good = False
        ok.append(good)
        X.append(vec if good else [0.0] * len(feats))
        y.append([float(u) if u is not None else 0.0, float(d) if d is not None else 0.0])
    return np.asarray(X, dtype=float), np.asarray(y, dtype=float), np.asarray(ok, dtype=bool)


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


def train_pop(rows: list[dict[str, Any]], feats: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if not r.get("common_cohort"):
            continue
        if _f(r.get("T1")) is None or _f(r.get("T2")) is None:
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
        if _f(r.get("pred_U")) is None or _f(r.get("pred_D")) is None:
            continue
        if _f(r.get("T1")) is None or _f(r.get("T2")) is None:
            continue
        if feats and not spec_features_ok(r, feats):
            continue
        out.append(r)
    return out


def attach_joint_ranks(grp: list[dict[str, Any]]) -> None:
    pu = [float(r["pred_U"]) for r in grp]
    pd = [float(r["pred_D"]) for r in grp]
    n = max(len(grp), 1)
    ru = rankdata(np.asarray(pu, dtype=float), method="average") / float(n)
    rd = rankdata(np.asarray(pd, dtype=float), method="average") / float(n)
    for r, a, b in zip(grp, ru, rd):
        r["RU"] = float(a)
        r["RD"] = float(b)
        r["ru_rd_mean"] = 0.5 * (float(a) + float(b))
        r["joint_score"] = float(min(float(a), float(b)))


def rank_joint(grp: list[dict[str, Any]]) -> list[dict[str, Any]]:
    xs = [r for r in grp if r.get("joint_score") is not None]
    xs.sort(
        key=lambda r: (
            -float(r["joint_score"]),
            -float(r["ru_rd_mean"]),
            str(r.get("symbol") or ""),
        )
    )
    return xs


def process_representation(payload: dict[str, Any]) -> dict[str, Any]:
    spec = dict(payload.get("spec") or {})
    rid = str(spec.get("representation_id") or "")
    rows_path = Path(payload["rows_path"])
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    print(f"  rf-rep start {rid}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(executable_rows(rows), feats, norm)
    scored: list[dict[str, Any]] = []
    fold_n = 0
    for hold in days:
        tr = train_pop([r for r in prepared if str(r.get("date")) != hold], feats)
        te = [r for r in prepared if str(r.get("date")) == hold]
        if len(tr) < 40 or not te:
            continue
        X, y, ok = _matrix(tr, feats)
        if int(ok.sum()) < 40:
            continue
        model = RandomForestRegressor(**RF_PARAMS)
        model.fit(X[ok], y[ok])
        Xt, tok = _feat_matrix(te, feats)
        preds = np.full((len(te), 2), np.nan, dtype=float)
        if int(tok.sum()) > 0:
            preds[tok] = model.predict(Xt[tok])
        for r, (pu, pd), good in zip(te, preds, tok):
            rec = dict(r)
            if not bool(good) or pu != pu or pd != pd:
                rec["pred_U"] = None
                rec["pred_D"] = None
            else:
                rec["pred_U"] = float(pu)
                rec["pred_D"] = float(pd)
            scored.append(rec)
        fold_n += 1
        print(f"    {rid} hold={hold} train={int(ok.sum())} te={len(te)}", flush=True)

    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in eval_pop(scored, feats):
        by[cohort_key(r)].append(r)

    daily_mfe: dict[str, list[float]] = defaultdict(list)
    daily_dn: dict[str, list[float]] = defaultdict(list)
    joint_flags = []
    spears = []
    psize = []
    pshare = []
    member_n = 0
    top_n = 0
    dominated_n = 0
    n_coh = 0
    for (date, _sess, _an), grp in sorted(by.items()):
        if len(grp) < int(MIN_COHORT_N):
            continue
        n_coh += 1
        attach_joint_ranks(grp)
        rf_top = rank_joint(grp)[:TOPK]
        cur_top = rank_group(grp, "current_score")[:TOPK]
        ru, rd = mean_ud(rf_top)
        cu, cd = mean_ud(cur_top)
        if ru is not None and cu is not None:
            daily_mfe[date].append(float(ru - cu))
        if rd is not None and cd is not None:
            daily_dn[date].append(float(rd - cd))
        if ru is not None and cu is not None and rd is not None and cd is not None:
            joint_flags.append(bool(ru > cu and rd > cd))
        pu = [float(r["pred_U"]) for r in grp]
        pd = [float(r["pred_D"]) for r in grp]
        sp = spearman(pu, pd)
        if sp is not None:
            spears.append(float(sp))
        front_pred = pareto_mask(pu, pd)
        psize.append(int(sum(1 for v in front_pred if v)))
        pshare.append(psize[-1] / float(len(grp)))
        au = [float(r["T1"]) for r in grp]
        ad = [float(r["T2"]) for r in grp]
        front_act = pareto_mask(au, ad)
        idx = {id(r): i for i, r in enumerate(grp)}
        for r in rf_top:
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
    mfe_vals = [float(r["value"]) for r in mfe_days]
    dn_vals = [float(r["value"]) for r in dn_days]
    mfe_st = delta_series_stats(mfe_vals)
    dn_st = delta_series_stats(dn_vals)
    print(
        f"  rf-rep done {rid} folds={fold_n} cohorts={n_coh} "
        f"mfe_d={mfe_st.get('mean')} dn_d={dn_st.get('mean')} "
        f"joint_rate={(sum(joint_flags) / len(joint_flags)) if joint_flags else None}",
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
        "JOINT_TOP3_MFE_DELTA": mfe_st.get("mean"),
        "JOINT_TOP3_DOWNSIDE_DELTA": dn_st.get("mean"),
        "JOINT_IMPROVEMENT_COHORT_RATE": (sum(joint_flags) / float(len(joint_flags))) if joint_flags else None,
        "MFE_POSITIVE_DAYS": mfe_st.get("positive_days"),
        "MFE_NEGATIVE_DAYS": mfe_st.get("negative_days"),
        "MFE_EX_BEST_DAY": mfe_st.get("ex_best_day"),
        "MFE_EX_TOP3_DAYS": mfe_st.get("ex_top3_days"),
        "DOWNSIDE_POSITIVE_DAYS": dn_st.get("positive_days"),
        "DOWNSIDE_NEGATIVE_DAYS": dn_st.get("negative_days"),
        "DOWNSIDE_EX_BEST_DAY": dn_st.get("ex_best_day"),
        "DOWNSIDE_EX_TOP3_DAYS": dn_st.get("ex_top3_days"),
        "PREDICTED_UD_SPEARMAN_MEDIAN": float(np.median(spears)) if spears else None,
        "PREDICTED_PARETO_SIZE_MEAN": float(np.mean(psize)) if psize else None,
        "PREDICTED_PARETO_SHARE_MEAN": float(np.mean(pshare)) if pshare else None,
        "ACTUAL_PARETO_MEMBER_RATE": (member_n / float(top_n)) if top_n else None,
        "ACTUAL_DOMINATED_RATE": (dominated_n / float(top_n)) if top_n else None,
        "daily_mfe": mfe_days,
        "daily_downside": dn_days,
    }

"""3-seed 18-day LODO for frozen causal TCN. No epoch/model selection on held-out day."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from research.c3_canonical_retrain.analyze import spec_features_ok
from research.canonical_entry_performance_rebase.analyze import _f, rank_group
from research.direct_joint_objective.oof import attach_joint_labels, eval_pop, train_pop
from research.entry_objective_redesign_c3 import MIN_COHORT_N
from research.entry_objective_redesign_c3.oof import executable_rows
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.multiobjective_feasibility.analyze import cohort_key, mean_ud, pareto_mask
from research.temporal_model_probe import ELIGIBLE_DAYS, STATIC_FEATURES, TOPK
from research.temporal_model_probe.train import apply_scaler, fit_scaler, predict_proba, rows_to_arrays, train_tcn


def _clf_metrics(y: list[int], p: list[float]) -> tuple[Optional[float], Optional[float]]:
    if len(y) < 2 or len(y) != len(p):
        return None, None
    ap = float(average_precision_score(y, p))
    if len(set(y)) < 2:
        return None, ap
    return float(roc_auc_score(y, p)), ap


def evaluate_scored(scored: list[dict[str, Any]], days: list[str], feats: list[str]) -> dict[str, Any]:
    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in eval_pop(scored, feats):
        by[cohort_key(r)].append(r)
    daily_mfe: dict[str, list[float]] = defaultdict(list)
    daily_dn: dict[str, list[float]] = defaultdict(list)
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
            joint_flags.append(bool(ru > cu and rd > cd))
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
    mfe_st = delta_series_stats([float(r["value"]) for r in mfe_days])
    dn_st = delta_series_stats([float(r["value"]) for r in dn_days])
    auc, ap = _clf_metrics(y_true, y_score)
    return {
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
    }


def process_seed(payload: dict[str, Any]) -> dict[str, Any]:
    seed = int(payload["seed"])
    rows_path = Path(payload["rows_path"])
    days = [str(d) for d in (payload.get("days") or ELIGIBLE_DAYS)]
    print(f"  tcn start seed={seed}", flush=True)
    body = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = list(body.get("rows") or [])
    feats = list(STATIC_FEATURES)
    label_stats = attach_joint_labels(rows)
    prepared = executable_rows(rows)
    scored: list[dict[str, Any]] = []
    fold_n = 0
    leak_n = 0
    for hold in days:
        tr = train_pop([r for r in prepared if str(r.get("date")) != hold], feats)
        te = [r for r in prepared if str(r.get("date")) == hold]
        te = [r for r in te if spec_features_ok(r, feats)]
        if any(str(r.get("date")) == hold for r in tr):
            leak_n += 1
        if len(tr) < 40 or not te:
            continue
        seq_tr, st_tr, y_tr = rows_to_arrays(tr)
        scaler = fit_scaler(seq_tr, st_tr)
        seq_tr_z, st_tr_z = apply_scaler(seq_tr, st_tr, scaler)
        model = train_tcn(seq=seq_tr_z, static=st_tr_z, y=y_tr, seed=seed)
        seq_te, st_te, _y_te = rows_to_arrays(te)
        seq_te_z, st_te_z = apply_scaler(seq_te, st_te, scaler)
        proba = predict_proba(model, seq_te_z, st_te_z)
        for r, pr in zip(te, proba):
            rec = dict(r)
            rec["joint_score"] = float(pr) if pr == pr else None
            scored.append(rec)
        fold_n += 1
        print(
            f"    seed={seed} hold={hold} train={len(tr)} pos={int(y_tr.sum())} te={len(te)}",
            flush=True,
        )
    metrics = evaluate_scored(scored, days, feats)
    print(
        f"  tcn done seed={seed} folds={fold_n} cohorts={metrics.get('n_cohorts')} "
        f"mfe={metrics.get('TOP3_MFE_DELTA')} dn={metrics.get('TOP3_DOWNSIDE_DELTA')} "
        f"joint={metrics.get('JOINT_COHORT_SUCCESS_RATE')}",
        flush=True,
    )
    return {
        "ok": True,
        "seed": seed,
        "outer_folds": fold_n,
        "heldout_fit_leak_n": int(leak_n),
        **metrics,
        **label_stats,
    }

"""Chronological walk-forward for multiclass Winner model."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.labels import (
    CLASS_ORDER,
    MulticlassRow,
    class_counts,
    label_multiclass,
    winner_threshold,
    y_ids,
)
from research.winner_multiclass.lanes import lane_of
from research.winner_multiclass.matrix import build_xy
from research.winner_multiclass.models import _proba, eval_multiclass, fit_models
from research.winner_multiclass.scores import estimate_payoffs_train, scores_from_proba


def _pf(pnls: np.ndarray) -> Optional[float]:
    g = float(pnls[pnls > 0].sum()) if (pnls > 0).any() else 0.0
    l = float(-pnls[pnls < 0].sum()) if (pnls < 0).any() else 0.0
    if l <= 1e-12:
        return None if g <= 0 else 99.0
    return round(g / l, 4)


def keep_by_score(scores: np.ndarray, keep_rate: float) -> np.ndarray:
    if keep_rate >= 0.999:
        return np.ones(len(scores), dtype=bool)
    if len(scores) == 0:
        return np.zeros(0, dtype=bool)
    thr = float(np.quantile(scores, 1.0 - keep_rate))
    return scores >= thr


def portfolio_metrics(
    labeled: Sequence[MulticlassRow],
    kept: np.ndarray,
) -> dict[str, Any]:
    n = len(labeled)
    k = int(kept.sum())
    if k == 0:
        return {
            "trades": 0,
            "keep_rate": 0.0,
            "total_pnl_raw": 0.0,
            "total_pnl_5bps": 0.0,
            "PF": None,
            "mean_pnl": None,
            "winner_rate": 0.0,
            "STOP率": 0.0,
            "NoProgress率": 0.0,
            "Winner捕捉率": 0.0,
            "Winner犠牲率": 1.0,
        }
    pnls = np.array([r.pnl_yen_100 for r in labeled])
    pnl5 = np.array([r.pnl_5bps for r in labeled])
    labs = np.array([r.class_label for r in labeled])
    true_w = labs == "Winner"
    n_w = max(int(true_w.sum()), 1)
    sub_lab = labs[kept]
    return {
        "trades": k,
        "keep_rate": round(k / n, 4),
        "total_pnl_raw": round(float(pnls[kept].sum()), 2),
        "total_pnl_5bps": round(float(pnl5[kept].sum()), 2),
        "PF": _pf(pnl5[kept]),
        "mean_pnl": round(float(pnls[kept].mean()), 2),
        "winner_rate": round(float((sub_lab == "Winner").sum()) / k, 4),
        "STOP率": round(float((sub_lab == "STOP").sum()) / k, 4),
        "NoProgress率": round(float((sub_lab == "NoProgress").sum()) / k, 4),
        "Winner捕捉率": round(float((kept & true_w).sum()) / n_w, 4),
        "Winner犠牲率": round(float((~kept & true_w).sum()) / n_w, 4),
    }


def daily_stability(labeled: Sequence[MulticlassRow], kept: np.ndarray) -> dict[str, Any]:
    by: dict[str, list[float]] = {}
    for i, r in enumerate(labeled):
        if kept[i]:
            by.setdefault(r.trade.day, []).append(r.pnl_5bps)
    daily = [{"day": d, "pnl_5bps": round(sum(xs), 2), "n": len(xs)} for d, xs in sorted(by.items())]
    if not daily:
        return {"pos_days": 0, "neg_days": 0, "max_daily_loss": None, "max_losing_streak_days": 0, "daily": []}
    signs = [1 if x["pnl_5bps"] > 0 else (-1 if x["pnl_5bps"] < 0 else 0) for x in daily]
    streak = mx = 0
    for s in signs:
        if s < 0:
            streak += 1
            mx = max(mx, streak)
        else:
            streak = 0
    losses = [x["pnl_5bps"] for x in daily if x["pnl_5bps"] < 0]
    return {
        "pos_days": sum(1 for x in daily if x["pnl_5bps"] > 0),
        "neg_days": sum(1 for x in daily if x["pnl_5bps"] < 0),
        "max_daily_loss": round(min(losses), 2) if losses else 0.0,
        "max_losing_streak_days": mx,
        "daily": daily,
    }


def chronological_walk_forward(
    labeled: Sequence[MulticlassRow],
    rows: Sequence[Mapping[str, Optional[float]]],
    feature_names: Sequence[str],
    *,
    model_name: str = "lightgbm",
    impute_lanes: Sequence[str] = ("A",),
    min_train_n: int = 120,
    keep_rate_for_fold: float = 0.25,
    score_key: str = "entry_quality_score",
) -> dict[str, Any]:
    days = sorted({r.trade.day for r in labeled})
    folds = []
    oos_scores = np.full(len(labeled), np.nan)
    oos_proba = np.full((len(labeled), len(CLASS_ORDER)), np.nan)
    oos_keep = np.zeros(len(labeled), dtype=bool)

    for d in days:
        tr_idx = [i for i, r in enumerate(labeled) if r.trade.day < d]
        te_idx = [i for i, r in enumerate(labeled) if r.trade.day == d]
        if len(tr_idx) < min_train_n or len(te_idx) < 1:
            folds.append(
                {
                    "train_start": days[0],
                    "train_end": None,
                    "test_date": d,
                    "status": "SKIP",
                    "train_n": len(tr_idx),
                    "test_n": len(te_idx),
                }
            )
            continue
        tr_days = sorted({labeled[i].trade.day for i in tr_idx})
        thr = winner_threshold([labeled[i].trade for i in tr_idx])
        fold_trades = [labeled[i].trade for i in tr_idx + te_idx]
        fold_rows_lab = label_multiclass(fold_trades, winner_thr=thr)
        y_fold = y_ids(fold_rows_lab)
        rows_fold = [rows[i] for i in tr_idx + te_idx]
        local_tr = list(range(len(tr_idx)))
        local_te = list(range(len(tr_idx), len(tr_idx) + len(te_idx)))

        # Fold feature set: keep columns with train support.
        # Lane A: train-median impute. Lane B/C: observed-only (no impute).
        # Columns with zero train observations are dropped for this fold (no synthetic fill).
        use_feats: list[str] = []
        medians: dict[str, float] = {}
        impute_set = set(impute_lanes)
        for k in feature_names:
            tr_vals = []
            for i in local_tr:
                v = rows_fold[i].get(k)
                if v is not None:
                    tr_vals.append(float(v))
            if not tr_vals:
                continue
            use_feats.append(k)
            if lane_of(k) in impute_set:
                medians[k] = float(np.median(tr_vals))
        if len(use_feats) < 3:
            folds.append(
                {
                    "train_start": tr_days[0],
                    "train_end": tr_days[-1],
                    "test_date": d,
                    "status": "SKIP_NO_FEATURES",
                    "train_n": len(tr_idx),
                    "test_n": len(te_idx),
                }
            )
            continue

        X_all = np.full((len(rows_fold), len(use_feats)), np.nan)
        for i, r in enumerate(rows_fold):
            for j, k in enumerate(use_feats):
                v = r.get(k)
                if v is not None:
                    X_all[i, j] = float(v)
                elif k in medians:
                    X_all[i, j] = medians[k]
        complete = ~np.isnan(X_all).any(axis=1)
        tr_ok_local = [i for i in local_tr if complete[i]]
        te_ok_local = [i for i in local_te if complete[i]]
        if len(tr_ok_local) < 80 or len(te_ok_local) < 1:
            folds.append(
                {
                    "train_start": tr_days[0],
                    "train_end": tr_days[-1],
                    "test_date": d,
                    "status": "SKIP_INCOMPLETE",
                    "train_n": len(tr_idx),
                    "train_complete_n": len(tr_ok_local),
                    "test_n": len(te_idx),
                    "test_complete_n": len(te_ok_local),
                }
            )
            continue

        Xtr = X_all[tr_ok_local]
        ytr = y_fold[tr_ok_local]
        Xte = X_all[te_ok_local]
        yte = y_fold[te_ok_local]
        models = fit_models(Xtr, ytr)
        model = models.get(model_name) or models.get("lightgbm") or models.get("random_forest")
        if model is None or not hasattr(model, "predict"):
            folds.append({"test_date": d, "status": "SKIP_MODEL", "train_n": len(tr_ok_local)})
            continue
        proba = _proba(model, Xte)
        pred = model.predict(Xte)
        mcls = eval_multiclass(yte, proba, pred)
        payoffs = estimate_payoffs_train([fold_rows_lab[i] for i in tr_ok_local])
        sc = scores_from_proba(proba, payoffs)
        score = sc[score_key]
        kept_local = keep_by_score(score, keep_rate_for_fold)
        te_lab = [fold_rows_lab[i] for i in te_ok_local]
        for j, li in enumerate(te_ok_local):
            gi = te_idx[li - len(tr_idx)]
            oos_scores[gi] = score[j]
            oos_proba[gi] = proba[j]
            oos_keep[gi] = bool(kept_local[j])
        port = portfolio_metrics(te_lab, kept_local)
        stab = daily_stability(te_lab, kept_local)
        base = portfolio_metrics(te_lab, np.ones(len(te_ok_local), dtype=bool))
        lane_tag = "/".join(sorted({lane_of(f) for f in feature_names}))
        folds.append(
            {
                "train_start": tr_days[0],
                "train_end": tr_days[-1],
                "test_date": d,
                "status": "EVAL",
                "train_n": len(tr_ok_local),
                "test_n": len(te_ok_local),
                "lane": lane_tag,
                "model": model_name,
                "feature_set_n": len(use_feats),
                "winner_threshold_train": thr,
                "class_counts_train": class_counts([fold_rows_lab[i] for i in tr_ok_local]),
                "class_counts_test": class_counts(te_lab),
                "macro_f1": mcls["macro_f1"],
                "winner_precision": mcls["winner_precision"],
                "winner_recall": mcls["winner_recall"],
                "stop_recall": mcls["stop_recall"],
                "no_progress_recall": mcls["no_progress_recall"],
                "keep_rate": port["keep_rate"],
                "PnL_5bps": port["total_pnl_5bps"],
                "PF": port["PF"],
                "winner_rate": port["winner_rate"],
                "STOP率": port["STOP率"],
                "NoProgress率": port["NoProgress率"],
                "Winner犠牲率": port["Winner犠牲率"],
                "base_PnL_5bps": base["total_pnl_5bps"],
                "base_PF": base["PF"],
                "base_STOP率": base["STOP率"],
                "base_NoProgress率": base["NoProgress率"],
                "delta_PnL_5bps": round(port["total_pnl_5bps"] - base["total_pnl_5bps"], 2),
                "pos_days": stab["pos_days"],
                "neg_days": stab["neg_days"],
                "max_daily_loss": stab["max_daily_loss"],
                "payoffs_train": payoffs,
            }
        )

    eval_folds = [f for f in folds if f.get("status") == "EVAL"]
    if eval_folds:
        summary = {
            "n_eval_folds": len(eval_folds),
            "n_skip": sum(1 for f in folds if f.get("status") != "EVAL"),
            "total_PnL_5bps": round(sum(f["PnL_5bps"] for f in eval_folds), 2),
            "base_total_PnL_5bps": round(sum(f["base_PnL_5bps"] for f in eval_folds), 2),
            "delta_PnL_5bps": round(
                sum(f["PnL_5bps"] for f in eval_folds) - sum(f["base_PnL_5bps"] for f in eval_folds), 2
            ),
            "pos_days": sum(f["pos_days"] for f in eval_folds),
            "neg_days": sum(f["neg_days"] for f in eval_folds),
            "median_PF": float(np.median([f["PF"] for f in eval_folds if f.get("PF") is not None] or [0])),
            "median_STOP": float(np.median([f["STOP率"] for f in eval_folds])),
            "median_NP": float(np.median([f["NoProgress率"] for f in eval_folds])),
            "median_winner_precision": float(np.median([f["winner_precision"] for f in eval_folds])),
            "median_macro_f1": float(np.median([f["macro_f1"] for f in eval_folds])),
        }
    else:
        summary = {"n_eval_folds": 0}
    return {
        "folds": folds,
        "summary": summary,
        "oos_scores": oos_scores,
        "oos_proba": oos_proba,
        "oos_keep": oos_keep,
        "model_name": model_name,
        "score_key": score_key,
        "keep_rate_for_fold": keep_rate_for_fold,
    }


def keep_rate_sensitivity_oos(
    labeled: Sequence[MulticlassRow],
    oos_scores: np.ndarray,
    rates: Sequence[float] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 1.0),
) -> list[dict[str, Any]]:
    valid = ~np.isnan(oos_scores)
    lab = [labeled[i] for i in range(len(labeled)) if valid[i]]
    sc = oos_scores[valid]
    out = []
    for kr in rates:
        kept = keep_by_score(sc, kr)
        m = portfolio_metrics(lab, kept)
        stab = daily_stability(lab, kept)
        # CAP usage
        caps = []
        idxs = [i for i in range(len(labeled)) if valid[i]]
        for j, gi in enumerate(idxs):
            if not kept[j]:
                continue
            v = labeled[gi].trade.features.get("f_cap_usage")
            if v is not None:
                caps.append(float(v))
        out.append(
            {
                "keep_rate_target": kr,
                **m,
                "pos_days": stab["pos_days"],
                "neg_days": stab["neg_days"],
                "max_daily_loss": stab["max_daily_loss"],
                "max_losing_streak_days": stab["max_losing_streak_days"],
                "cap_usage_mean": round(float(np.mean(caps)), 4) if caps else None,
                "universe_n": len(lab),
            }
        )
    return out

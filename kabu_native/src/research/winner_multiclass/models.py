"""Multiclass model training and metrics."""
from __future__ import annotations

from typing import Any, Optional, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.calibration import calibration_curve
from sklearn.preprocessing import label_binarize

from research.winner_multiclass.labels import CLASS_ORDER, CLASS_TO_ID


def _proba(model: Any, X: np.ndarray, *, n_cls: int = 4) -> np.ndarray:
    """Return proba aligned to CLASS_ORDER ids 0..n_cls-1."""
    if hasattr(model, "predict_proba"):
        raw = model.predict_proba(X)
        classes = list(getattr(model, "classes_", range(raw.shape[1])))
        out = np.zeros((len(X), n_cls), dtype=float)
        for j, c in enumerate(classes):
            ci = int(c)
            if 0 <= ci < n_cls:
                out[:, ci] = raw[:, j]
        # renormalize if some classes absent
        s = out.sum(axis=1, keepdims=True)
        s[s <= 0] = 1.0
        return out / s
    d = model.decision_function(X)
    if d.ndim == 1:
        p = 1 / (1 + np.exp(-d))
        return np.column_stack([1 - p, p])
    e = np.exp(d - d.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def eval_multiclass(y_true: np.ndarray, proba: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    n_cls = len(CLASS_ORDER)
    # align proba columns to CLASS_ORDER ids 0..3
    if proba.shape[1] != n_cls:
        # pad
        pp = np.zeros((len(y_true), n_cls))
        pp[:, : proba.shape[1]] = proba
        proba = pp
    report = classification_report(
        y_true, y_pred, labels=list(range(n_cls)), target_names=list(CLASS_ORDER), output_dict=True, zero_division=0
    )
    prec, rec, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=list(range(n_cls)), zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred, labels=list(range(n_cls))).tolist()
    # macro AUC ovr
    try:
        y_bin = label_binarize(y_true, classes=list(range(n_cls)))
        if y_bin.shape[1] == 1:
            macro_auc = None
        else:
            macro_auc = float(roc_auc_score(y_bin, proba, average="macro", multi_class="ovr"))
    except Exception:
        macro_auc = None

    # Winner-focused
    w_id = CLASS_TO_ID["Winner"]
    s_id = CLASS_TO_ID["STOP"]
    n_id = CLASS_TO_ID["NoProgress"]
    pred_w = y_pred == w_id
    true_w = y_true == w_id
    false_winner_rate = float((pred_w & ~true_w).sum() / max(pred_w.sum(), 1))
    winner_sacrifice = float((~pred_w & true_w).sum() / max(true_w.sum(), 1))  # among true winners, missed
    stop_missed = float(((y_true == s_id) & (y_pred != s_id)).sum() / max((y_true == s_id).sum(), 1))

    # calibration (winner class)
    calib = None
    try:
        if true_w.sum() > 5 and (~true_w).sum() > 5:
            frac_pos, mean_pred = calibration_curve(true_w.astype(int), proba[:, w_id], n_bins=8, strategy="quantile")
            calib = {
                "winner_fraction_positives": [round(float(x), 4) for x in frac_pos],
                "winner_mean_predicted": [round(float(x), 4) for x in mean_pred],
            }
    except Exception:
        pass

    try:
        ll = float(log_loss(y_true, proba, labels=list(range(n_cls))))
    except Exception:
        ll = None

    return {
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "weighted_f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 6),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 6),
        "log_loss": ll,
        "macro_auc_ovr": None if macro_auc is None else round(macro_auc, 6),
        "confusion_matrix": cm,
        "class_precision": {CLASS_ORDER[i]: round(float(prec[i]), 6) for i in range(n_cls)},
        "class_recall": {CLASS_ORDER[i]: round(float(rec[i]), 6) for i in range(n_cls)},
        "class_f1": {CLASS_ORDER[i]: round(float(f1[i]), 6) for i in range(n_cls)},
        "class_support": {CLASS_ORDER[i]: int(support[i]) for i in range(n_cls)},
        "winner_precision": round(float(prec[w_id]), 6),
        "winner_recall": round(float(rec[w_id]), 6),
        "stop_recall": round(float(rec[s_id]), 6),
        "no_progress_recall": round(float(rec[n_id]), 6),
        "false_winner_rate": round(false_winner_rate, 6),
        "winner_sacrifice_rate": round(winner_sacrifice, 6),
        "stop_missed_rate": round(stop_missed, 6),
        "calibration": calib,
        "classification_report": report,
    }


def fit_models(X_tr: np.ndarray, y_tr: np.ndarray) -> dict[str, Any]:
    models: dict[str, Any] = {}
    # 1. Multinomial LR
    models["logistic"] = LogisticRegression(
        max_iter=400, class_weight="balanced", solver="lbfgs"
    )
    models["logistic"].fit(X_tr, y_tr)
    # 2. RF
    models["random_forest"] = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=10, class_weight="balanced_subsample", random_state=42, n_jobs=-1
    )
    models["random_forest"].fit(X_tr, y_tr)
    # 3. LightGBM
    try:
        import lightgbm as lgb

        models["lightgbm"] = lgb.LGBMClassifier(
            objective="multiclass",
            num_class=len(CLASS_ORDER),
            n_estimators=250,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            verbosity=-1,
        )
        models["lightgbm"].fit(X_tr, y_tr)
    except Exception as e:
        models["lightgbm_error"] = str(e)[:120]
    # XGB / CatBoost unavailable in env — recorded as skipped
    return models


def compare_models_holdout(
    X: np.ndarray,
    y: np.ndarray,
    *,
    split: float = 0.8,
) -> tuple[dict[str, Any], Any, str]:
    n = len(y)
    cut = max(int(n * split), n - max(40, n // 5))
    if cut >= n - 10:
        cut = int(n * 0.75)
    X_tr, y_tr = X[:cut], y[:cut]
    X_te, y_te = X[cut:], y[cut:]
    models = fit_models(X_tr, y_tr)
    results = {}
    best_name, best_score, best_model = None, -1.0, None
    for name, model in models.items():
        if name.endswith("_error") or not hasattr(model, "predict"):
            results[name] = {"error": models.get(name)}
            continue
        proba = _proba(model, X_te)
        pred = model.predict(X_te)
        m = eval_multiclass(y_te, proba, pred)
        m["n_train"] = int(len(y_tr))
        m["n_test"] = int(len(y_te))
        results[name] = m
        score = (m["macro_f1"] or 0) + 0.15 * (m["winner_precision"] or 0) + 0.10 * (m["stop_recall"] or 0)
        if score > best_score:
            best_score, best_name, best_model = score, name, model
    # refit best on all for downstream if needed
    if best_model is not None:
        best_model.fit(X, y)
    return results, best_model, best_name or "lightgbm"

"""Feature importance: LightGBM, Permutation, SHAP, Information Gain, Mutual Information."""
from __future__ import annotations

from typing import Any, Optional, Sequence

import numpy as np


def _safe_import_lgbm():
    import lightgbm as lgb

    return lgb


def information_gain_scores(X: np.ndarray, y: np.ndarray, feature_names: Sequence[str]) -> list[dict[str, Any]]:
    """Binary entropy IG via quantile binning (4 bins)."""
    y = y.astype(int)
    p1 = float(y.mean()) if len(y) else 0.0
    h_y = 0.0 if p1 <= 0 or p1 >= 1 else -(p1 * np.log2(p1) + (1 - p1) * np.log2(1 - p1))
    rows: list[dict[str, Any]] = []
    for j, name in enumerate(feature_names):
        col = X[:, j]
        try:
            qs = np.unique(np.nanquantile(col, [0.0, 0.25, 0.5, 0.75, 1.0]))
            if len(qs) < 3:
                rows.append({"feature": name, "information_gain": 0.0})
                continue
            bins = np.digitize(col, qs[1:-1], right=True)
        except Exception:
            rows.append({"feature": name, "information_gain": 0.0})
            continue
        h_cond = 0.0
        for b in np.unique(bins):
            mask = bins == b
            if not mask.any():
                continue
            pb = float(mask.mean())
            py = float(y[mask].mean())
            if 0 < py < 1:
                hb = -(py * np.log2(py) + (1 - py) * np.log2(1 - py))
            else:
                hb = 0.0
            h_cond += pb * hb
        rows.append({"feature": name, "information_gain": round(max(0.0, h_y - h_cond), 8)})
    rows.sort(key=lambda r: -r["information_gain"])
    return rows


def mutual_info_scores(X: np.ndarray, y: np.ndarray, feature_names: Sequence[str]) -> list[dict[str, Any]]:
    from sklearn.feature_selection import mutual_info_classif

    mi = mutual_info_classif(X, y, discrete_features=False, random_state=42, n_neighbors=5)
    rows = [{"feature": n, "mutual_info": round(float(v), 8)} for n, v in zip(feature_names, mi)]
    rows.sort(key=lambda r: -r["mutual_info"])
    return rows


def lightgbm_importance(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
) -> tuple[list[dict[str, Any]], Any, dict[str, Any]]:
    lgb = _safe_import_lgbm()
    # Chronological-ish split: last 20% as valid if enough rows
    n = len(y)
    split = max(int(n * 0.8), n - max(50, n // 5))
    if split >= n - 10:
        split = n
        X_tr, y_tr = X, y
        X_va, y_va = X, y
    else:
        X_tr, y_tr = X[:split], y[:split]
        X_va, y_va = X[split:], y[split:]

    params = {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": 42,
    }
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=list(feature_names), free_raw_data=False)
    dvalid = lgb.Dataset(X_va, label=y_va, reference=dtrain, free_raw_data=False)
    callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=0)]
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=400,
        valid_sets=[dvalid],
        callbacks=callbacks,
    )
    gain = model.feature_importance(importance_type="gain")
    split_imp = model.feature_importance(importance_type="split")
    rows = []
    for i, name in enumerate(feature_names):
        rows.append(
            {
                "feature": name,
                "lgbm_gain": float(gain[i]),
                "lgbm_split": float(split_imp[i]),
            }
        )
    rows.sort(key=lambda r: -r["lgbm_gain"])
    meta = {
        "best_iteration": int(getattr(model, "best_iteration", 0) or 0),
        "n_train": int(len(y_tr)),
        "n_valid": int(len(y_va)),
        "pos_rate_train": float(np.mean(y_tr)) if len(y_tr) else None,
        "pos_rate_valid": float(np.mean(y_va)) if len(y_va) else None,
    }
    try:
        from sklearn.metrics import roc_auc_score

        pred = model.predict(X_va)
        meta["valid_auc"] = float(roc_auc_score(y_va, pred)) if len(np.unique(y_va)) > 1 else None
    except Exception:
        meta["valid_auc"] = None
    return rows, model, meta


def permutation_importance_scores(
    model: Any,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    *,
    n_repeats: int = 5,
) -> list[dict[str, Any]]:
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import roc_auc_score

    class _Wrap:
        def __init__(self, m):
            self.m = m

        def fit(self, *_a, **_k):
            return self

        def predict(self, X_):
            return (self.m.predict(X_) >= 0.5).astype(int)

        def predict_proba(self, X_):
            p = self.m.predict(X_)
            return np.column_stack([1 - p, p])

        def score(self, X_, y_):
            p = self.m.predict(X_)
            if len(np.unique(y_)) < 2:
                return 0.0
            return float(roc_auc_score(y_, p))

    wrap = _Wrap(model)
    # Use a subsample for speed
    n = len(y)
    if n > 800:
        idx = np.linspace(0, n - 1, 800).astype(int)
        Xs, ys = X[idx], y[idx]
    else:
        Xs, ys = X, y
    r = permutation_importance(
        wrap,
        Xs,
        ys,
        n_repeats=n_repeats,
        random_state=42,
        scoring=None,
    )
    rows = []
    for i, name in enumerate(feature_names):
        rows.append(
            {
                "feature": name,
                "perm_importance_mean": round(float(r.importances_mean[i]), 8),
                "perm_importance_std": round(float(r.importances_std[i]), 8),
            }
        )
    rows.sort(key=lambda r: -r["perm_importance_mean"])
    return rows


def shap_importance(
    model: Any,
    X: np.ndarray,
    feature_names: Sequence[str],
    *,
    max_samples: int = 400,
) -> tuple[list[dict[str, Any]], Optional[np.ndarray]]:
    import shap

    n = len(X)
    if n > max_samples:
        idx = np.linspace(0, n - 1, max_samples).astype(int)
        Xs = X[idx]
    else:
        Xs = X
        idx = np.arange(n)
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(Xs)
    if isinstance(sv, list):
        # binary: take positive class
        sv = sv[1] if len(sv) > 1 else sv[0]
    mean_abs = np.mean(np.abs(sv), axis=0)
    rows = [
        {"feature": n, "shap_mean_abs": round(float(v), 8)}
        for n, v in zip(feature_names, mean_abs)
    ]
    rows.sort(key=lambda r: -r["shap_mean_abs"])
    return rows, np.asarray(sv)


def merge_importance_tables(
    feature_names: Sequence[str],
    *,
    lgbm: Sequence[dict[str, Any]],
    perm: Sequence[dict[str, Any]],
    shap_rows: Sequence[dict[str, Any]],
    ig: Sequence[dict[str, Any]],
    mi: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    def _map(rows: Sequence[dict[str, Any]], key: str) -> dict[str, float]:
        return {r["feature"]: float(r.get(key) or 0.0) for r in rows}

    m_gain = _map(lgbm, "lgbm_gain")
    m_split = _map(lgbm, "lgbm_split")
    m_perm = _map(perm, "perm_importance_mean")
    m_shap = _map(shap_rows, "shap_mean_abs")
    m_ig = _map(ig, "information_gain")
    m_mi = _map(mi, "mutual_info")

    def _rank(d: dict[str, float]) -> dict[str, float]:
        ordered = sorted(d.items(), key=lambda kv: -kv[1])
        n = max(len(ordered), 1)
        return {k: 1.0 - i / n for i, (k, _) in enumerate(ordered)}

    r_gain = _rank(m_gain)
    r_perm = _rank(m_perm)
    r_shap = _rank(m_shap)
    r_ig = _rank(m_ig)
    r_mi = _rank(m_mi)

    out = []
    for name in feature_names:
        consensus = (
            0.30 * r_gain.get(name, 0.0)
            + 0.25 * r_shap.get(name, 0.0)
            + 0.20 * r_perm.get(name, 0.0)
            + 0.15 * r_mi.get(name, 0.0)
            + 0.10 * r_ig.get(name, 0.0)
        )
        out.append(
            {
                "feature": name,
                "lgbm_gain": m_gain.get(name, 0.0),
                "lgbm_split": m_split.get(name, 0.0),
                "perm_importance_mean": m_perm.get(name, 0.0),
                "shap_mean_abs": m_shap.get(name, 0.0),
                "information_gain": m_ig.get(name, 0.0),
                "mutual_info": m_mi.get(name, 0.0),
                "consensus_score": round(consensus, 6),
            }
        )
    out.sort(key=lambda r: -r["consensus_score"])
    return out


def run_all_importance(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
) -> dict[str, Any]:
    ig = information_gain_scores(X, y, feature_names)
    mi = mutual_info_scores(X, y, feature_names)
    lgbm_rows, model, lgbm_meta = lightgbm_importance(X, y, feature_names)
    perm = permutation_importance_scores(model, X, y, feature_names)
    shap_rows, shap_values = shap_importance(model, X, feature_names)
    merged = merge_importance_tables(
        feature_names, lgbm=lgbm_rows, perm=perm, shap_rows=shap_rows, ig=ig, mi=mi
    )
    return {
        "lgbm_meta": lgbm_meta,
        "lgbm": lgbm_rows,
        "permutation": perm,
        "shap": shap_rows,
        "information_gain": ig,
        "mutual_info": mi,
        "merged": merged,
        "model": model,
        "shap_values": shap_values,
    }

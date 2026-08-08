"""Serialize / deserialize A1_FILL logistic allocator exactly."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

try:
    import sklearn
    SKLEARN_VERSION = sklearn.__version__
except Exception:
    SKLEARN_VERSION = "unknown"

from research.e1_x36_joint_allocator import FEATURE_SETS
from research.e1_x36_joint_allocator.models import _matrix, fit_allocator


def fit_a1_fill(train: list[dict], *, feature_set: str, reg: float) -> dict[str, Any]:
    return fit_allocator(train, family="A1_FILL", feature_set=feature_set, reg=float(reg))


def serialize_fill_model(fit: dict[str, Any], *, train: list[dict]) -> dict[str, Any]:
    """Exact numeric freeze of A1_FILL logistic + StandardScaler."""
    assert fit.get("kind") == "fill"
    clf: LogisticRegression = fit["model"]
    scaler: StandardScaler = fit["scaler"]
    feats = list(fit["features"])

    # training fingerprint inputs
    X, ok = _matrix(train, tuple(feats))
    y_all = np.asarray([int(r["FILL_1S"]) for r in train], dtype=int)
    X_ok = X[ok]
    y_ok = y_all[ok]

    body = {
        "model_class": "sklearn.linear_model.LogisticRegression",
        "family": "A1_FILL",
        "feature_set": fit["feature_set"],
        "feature_order": feats,
        "n_features": len(feats),
        "coefficients": [float(x) for x in np.asarray(clf.coef_).reshape(-1)],
        "intercept": float(np.asarray(clf.intercept_).reshape(-1)[0]),
        "classes": [int(c) for c in clf.classes_],
        "regularization_C": float(fit["reg"]),
        "penalty": "l2",
        "solver": "lbfgs",
        "max_iter": 500,
        "random_seed": None,  # X36 default — no random_state set
        "n_iter_": int(clf.n_iter_[0]) if hasattr(clf, "n_iter_") else None,
        "converged": True if (hasattr(clf, "n_iter_") and int(clf.n_iter_[0]) < 500) else None,
        "preprocessing": {
            "type": "StandardScaler",
            "with_mean": True,
            "with_std": True,
            "mean": [float(x) for x in scaler.mean_],
            "scale": [float(x) for x in scaler.scale_],
            "var": [float(x) for x in scaler.var_],
            "n_samples_seen": int(scaler.n_samples_seen_),
        },
        "missing_value_handling": {
            "train": "exclude_row_if_any_feature_nonfinite",
            "score": "assign_score_neg_inf_if_any_feature_nonfinite",
        },
        "clipping_winsorization": None,
        "categorical_handling": None,
        "symbol_identity_feature": False,
        "sklearn_version": SKLEARN_VERSION,
        "numpy_version": np.__version__,
        "label": "FILL_1S",
        "score_semantic": "predict_proba_class_1",
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["model_artifact_sha256"] = hashlib.sha256(raw).hexdigest()
    # attach fingerprints separately (not in model artifact hash of coeffs alone — include in panel)
    body["_train_X_ok_shape"] = list(X_ok.shape)
    body["_train_y_ok_sum"] = int(y_ok.sum())
    return body


def score_fn_from_serialized(ser: dict[str, Any]) -> Callable[[dict], float]:
    feats = list(ser["feature_order"])
    scaler = StandardScaler()
    scaler.mean_ = np.asarray(ser["preprocessing"]["mean"], dtype=float)
    scaler.scale_ = np.asarray(ser["preprocessing"]["scale"], dtype=float)
    scaler.var_ = np.asarray(ser["preprocessing"]["var"], dtype=float)
    scaler.n_features_in_ = len(feats)
    scaler.n_samples_seen_ = int(ser["preprocessing"]["n_samples_seen"])

    clf = LogisticRegression(C=float(ser["regularization_C"]), max_iter=500, solver="lbfgs")
    # minimal fitted state for predict_proba
    clf.classes_ = np.asarray(ser["classes"], dtype=int)
    clf.coef_ = np.asarray(ser["coefficients"], dtype=float).reshape(1, -1)
    clf.intercept_ = np.asarray([ser["intercept"]], dtype=float)
    clf.n_features_in_ = len(feats)

    def _s(e: dict) -> float:
        vec = []
        for f in feats:
            v = e.get(f)
            if v is None or not np.isfinite(v):
                return float("-inf")
            vec.append(float(v))
        x = np.asarray(vec, dtype=float).reshape(1, -1)
        xs = scaler.transform(x)
        return float(clf.predict_proba(xs)[0, 1])

    return _s


def training_panel_fingerprint(train: list[dict], features: tuple[str, ...]) -> dict[str, Any]:
    days = sorted({e["date"] for e in train})
    symbols = sorted({e["symbol"] for e in train})
    # row order: date ASC, signal_time ASC, symbol ASC
    ordered = sorted(train, key=lambda e: (e["date"], float(e["signal_time"]), str(e["symbol"])))
    X, ok = _matrix(ordered, features)
    y = np.asarray([int(r["FILL_1S"]) for r in ordered], dtype=int)
    feat_bytes = X[ok].astype(np.float64).tobytes()
    lab_bytes = y[ok].astype(np.int64).tobytes()
    row_ids = "|".join(f"{e['date']}:{e['symbol']}:{e['signal_time']:.9f}" for e in ordered)
    payload = {
        "row_count": len(ordered),
        "day_list": days,
        "symbol_count": len(symbols),
        "feature_order": list(features),
        "row_order_rule": "date ASC, signal_time ASC, symbol ASC",
        "n_ok_rows": int(ok.sum()),
        "feature_matrix_sha256": hashlib.sha256(feat_bytes).hexdigest(),
        "label_fingerprint_sha256": hashlib.sha256(lab_bytes).hexdigest(),
        "row_id_sha256": hashlib.sha256(row_ids.encode()).hexdigest(),
        "forbidden_from": "20260810",
        "contains_20260810_plus": any(str(d) >= "20260810" for d in days),
    }
    raw = json.dumps({k: v for k, v in payload.items() if k != "sha256"}, sort_keys=True).encode()
    payload["sha256"] = hashlib.sha256(raw).hexdigest()
    return payload


def scores_from_fit(fit: dict, rows: list[dict]) -> list[float]:
    from research.e1_x36_joint_allocator.models import score_fn_from_fit
    sfn = score_fn_from_fit(fit)
    assert sfn is not None
    return [float(sfn(e)) for e in rows]

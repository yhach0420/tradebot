"""Frozen Ridge / RF / pairwise utility models. No hyperparameter search."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from research.am_entry_profit_improvement import (
    ARCH_PAIR,
    ARCH_RF,
    ARCH_RIDGE,
    LOGREG_PARAMS,
    RIDGE_ALPHA,
    SCORE_KEY,
    UTILITY_KEY,
    UTILITY_FORBIDDEN,
)
from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_objective_redesign_c3.oof import _predict_row, apply_norm, cohorts, normalize_rows
from research.wait5_session_target_learnability import RF_REG_PARAMS
from research.wait5_session_target_learnability.oof import _feat_matrix


def spec_grid(reps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for arch in (ARCH_RIDGE, ARCH_RF, ARCH_PAIR):
        for rep in reps:
            rec = dict(rep)
            rec["architecture_id"] = arch
            rec["spec_id"] = f"{arch}|{rep.get('representation_id')}"
            rec["alpha"] = float(RIDGE_ALPHA) if arch == ARCH_RIDGE else None
            rec["logreg"] = dict(LOGREG_PARAMS) if arch == ARCH_PAIR else None
            rec["rf"] = dict(RF_REG_PARAMS) if arch == ARCH_RF else None
            out.append(rec)
    return out


def contamination_n(feats: list[str]) -> int:
    n = 0
    for f in feats:
        if str(f) in UTILITY_FORBIDDEN:
            n += 1
    return n


def _y_ok(r: dict[str, Any]) -> bool:
    return _f(r.get(UTILITY_KEY)) is not None


def fit_ridge_utility(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = [r for r in normalize_rows(train, feats, norm) if _y_ok(r)]
    X, feat_ok = _feat_matrix(prepared, feats)
    y = np.asarray([float(r[UTILITY_KEY]) for r in prepared], dtype=float)
    ok = feat_ok
    if int(ok.sum()) < 40:
        return {
            "kind": "fail",
            "architecture_id": ARCH_RIDGE,
            "features": feats,
            "normalization": norm,
            "alpha": float(RIDGE_ALPHA),
        }
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[ok])
    m = Ridge(alpha=float(RIDGE_ALPHA))
    m.fit(Xs, y[ok])
    return {
        "kind": "ridge",
        "architecture_id": ARCH_RIDGE,
        "features": feats,
        "normalization": norm,
        "alpha": float(RIDGE_ALPHA),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": m.coef_.tolist(),
        "intercept": float(m.intercept_),
        "train_n": int(ok.sum()),
    }


def fit_rf_utility(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = [r for r in normalize_rows(train, feats, norm) if _y_ok(r)]
    X, feat_ok = _feat_matrix(prepared, feats)
    y = np.asarray([float(r[UTILITY_KEY]) for r in prepared], dtype=float)
    ok = feat_ok
    if int(ok.sum()) < 40:
        return {
            "kind": "fail",
            "architecture_id": ARCH_RF,
            "features": feats,
            "normalization": norm,
        }
    model = RandomForestRegressor(**RF_REG_PARAMS)
    model.fit(X[ok], y[ok])
    return {
        "kind": "rf",
        "architecture_id": ARCH_RF,
        "features": feats,
        "normalization": norm,
        "model": model,
        "train_n": int(ok.sum()),
    }


def _pair_xy(rows: list[dict[str, Any]], feats: list[str]) -> tuple[np.ndarray, np.ndarray]:
    xs: list[list[float]] = []
    ys: list[int] = []
    for grp in cohorts(rows).values():
        usable = []
        for r in grp:
            if not _y_ok(r):
                continue
            vec = []
            good = True
            for f in feats:
                v = _f(r.get(f))
                if v is None:
                    good = False
                    break
                vec.append(float(v))
            if good:
                usable.append((vec, float(r[UTILITY_KEY])))
        n = len(usable)
        if n < 2:
            continue
        F = np.asarray([u[0] for u in usable], dtype=float)
        U = np.asarray([u[1] for u in usable], dtype=float)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if U[i] == U[j]:
                    continue
                xs.append((F[i] - F[j]).tolist())
                ys.append(1 if U[i] > U[j] else 0)
    if not xs:
        return np.zeros((0, len(feats)), dtype=float), np.zeros((0,), dtype=int)
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=int)


def fit_pairwise(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = normalize_rows(train, feats, norm)
    X, y = _pair_xy(prepared, feats)
    if X.shape[0] < 40 or int(np.unique(y).size) < 2:
        return {
            "kind": "fail",
            "architecture_id": ARCH_PAIR,
            "features": feats,
            "normalization": norm,
        }
    model = LogisticRegression(**LOGREG_PARAMS)
    model.fit(X, y)
    return {
        "kind": "pairwise",
        "architecture_id": ARCH_PAIR,
        "features": feats,
        "normalization": norm,
        "model": model,
        "train_n": int(X.shape[0]),
    }


def fit_spec(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    arch = str(spec.get("architecture_id") or "")
    if arch == ARCH_RIDGE:
        return fit_ridge_utility(train, spec)
    if arch == ARCH_RF:
        return fit_rf_utility(train, spec)
    if arch == ARCH_PAIR:
        return fit_pairwise(train, spec)
    return {"kind": "fail", "architecture_id": arch, "features": list(spec.get("features") or [])}


def score_spec(rows: list[dict[str, Any]], fit: dict[str, Any], *, key: str = SCORE_KEY) -> list[dict[str, Any]]:
    feats = list(fit.get("features") or [])
    norm = str(fit.get("normalization") or "none")
    kind = str(fit.get("kind") or "fail")
    out: list[dict[str, Any]] = []
    for grp in cohorts(rows).values():
        normed = apply_norm(grp, feats, norm) if kind != "fail" else [dict(r) for r in grp]
        if kind == "ridge":
            for r in normed:
                rec = dict(r)
                rec[key] = _predict_row(fit, rec)
                out.append(rec)
            continue
        if kind == "rf":
            model = fit.get("model")
            X, ok = _feat_matrix(normed, feats)
            pred = model.predict(X) if model is not None and X.size else np.zeros((len(normed),), dtype=float)
            for r, p, g in zip(normed, pred, ok):
                rec = dict(r)
                rec[key] = float(p) if bool(g) else None
                out.append(rec)
            continue
        if kind == "pairwise":
            model = fit.get("model")
            usable: list[tuple[int, list[float]]] = []
            recs = [dict(r) for r in normed]
            for i, r in enumerate(recs):
                vec = []
                good = True
                for f in feats:
                    v = _f(r.get(f))
                    if v is None:
                        good = False
                        break
                    vec.append(float(v))
                r[key] = None
                if good:
                    usable.append((i, vec))
            if model is not None and len(usable) >= 2:
                F = np.asarray([v for _i, v in usable], dtype=float)
                n = F.shape[0]
                wins = np.zeros(n, dtype=float)
                for a in range(n):
                    diffs = F[a] - F
                    mask = np.ones(n, dtype=bool)
                    mask[a] = False
                    if int(mask.sum()) <= 0:
                        continue
                    proba = model.predict_proba(diffs[mask])
                    classes = [int(c) for c in model.classes_]
                    if 1 in classes:
                        wins[a] = float(np.mean(proba[:, classes.index(1)]))
                    else:
                        wins[a] = 0.0
                for k, (i, _v) in enumerate(usable):
                    recs[i][key] = float(wins[k])
            out.extend(recs)
            continue
        for r in normed:
            rec = dict(r)
            rec[key] = None
            out.append(rec)
    return out

"""Frozen multinomial LOGIT / RF. JOINT_SCORE = P_WIN - P_LOSS. No hyperparameter search."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from research.am_entry_information_expansion import (
    CLASS_LOSS,
    CLASS_NEUTRAL,
    CLASS_WIN,
    FAMILY_LOGIT,
    FAMILY_RF,
    JOINT_SCORE_KEY,
    LOGIT_PARAMS,
    RF_CLF_PARAMS,
    TARGET,
)
from research.am_entry_profit_improvement import UTILITY_FORBIDDEN
from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_objective_redesign_c3.oof import apply_norm, cohorts
from research.wait5_session_target_learnability.oof import _feat_matrix

FORBIDDEN = UTILITY_FORBIDDEN + (
    TARGET,
    CLASS_WIN,
    CLASS_LOSS,
    CLASS_NEUTRAL,
    JOINT_SCORE_KEY,
    "P_WIN",
    "P_LOSS",
    "P_NEUTRAL",
    "AUG_SCORE",
    "ENSEMBLE_JOINT_SCORE",
    "PROFITABLE_FILL_CLASS",
)


def contamination_n(feats: list[str]) -> int:
    n = 0
    for f in feats:
        if str(f) in FORBIDDEN:
            n += 1
    return n


def _y_ok(r: dict[str, Any]) -> bool:
    return str(r.get(TARGET) or "") in {CLASS_WIN, CLASS_LOSS, CLASS_NEUTRAL}


def _proba_joint(model: Any, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if X.size == 0 or model is None:
        z = np.zeros((0,), dtype=float)
        return z, z, z, z
    proba = np.asarray(model.predict_proba(X), dtype=float)
    names = [str(c) for c in model.classes_]
    idx = {n: i for i, n in enumerate(names)}
    n = proba.shape[0]
    p_win = proba[:, idx[CLASS_WIN]] if CLASS_WIN in idx else np.zeros(n, dtype=float)
    p_loss = proba[:, idx[CLASS_LOSS]] if CLASS_LOSS in idx else np.zeros(n, dtype=float)
    p_neu = proba[:, idx[CLASS_NEUTRAL]] if CLASS_NEUTRAL in idx else np.zeros(n, dtype=float)
    return p_win - p_loss, p_win, p_loss, p_neu


def fit_logit(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = [r for r in train if _y_ok(r)]
    prepared = _apply_norm(prepared, feats, norm)
    X, feat_ok = _feat_matrix(prepared, feats)
    y = np.asarray([str(r[TARGET]) for r in prepared], dtype=object)
    ok = feat_ok
    base = {
        "kind": "fail",
        "family": FAMILY_LOGIT,
        "features": feats,
        "normalization": norm,
        "logit": dict(LOGIT_PARAMS),
    }
    if int(ok.sum()) < 40 or len(set(y[ok].tolist())) < 2:
        return base
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[ok])
    clf = LogisticRegression(**LOGIT_PARAMS)
    clf.fit(Xs, y[ok])
    return {
        "kind": "logit",
        "family": FAMILY_LOGIT,
        "features": feats,
        "normalization": norm,
        "logit": dict(LOGIT_PARAMS),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "model": clf,
        "train_n": int(ok.sum()),
    }


def fit_rf(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    feats = list(spec.get("features") or [])
    norm = str(spec.get("normalization") or "none")
    prepared = [r for r in train if _y_ok(r)]
    prepared = _apply_norm(prepared, feats, norm)
    X, feat_ok = _feat_matrix(prepared, feats)
    y = np.asarray([str(r[TARGET]) for r in prepared], dtype=object)
    ok = feat_ok
    base = {
        "kind": "fail",
        "family": FAMILY_RF,
        "features": feats,
        "normalization": norm,
        "rf": dict(RF_CLF_PARAMS),
    }
    if int(ok.sum()) < 40 or len(set(y[ok].tolist())) < 2:
        return base
    params = dict(RF_CLF_PARAMS)
    params["n_jobs"] = 1
    clf = RandomForestClassifier(**params)
    clf.fit(X[ok], y[ok])
    return {
        "kind": "rf",
        "family": FAMILY_RF,
        "features": feats,
        "normalization": norm,
        "rf": dict(RF_CLF_PARAMS),
        "model": clf,
        "train_n": int(ok.sum()),
    }


def _apply_norm(rows: list[dict[str, Any]], feats: list[str], norm: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for grp in cohorts(rows).values():
        out.extend(apply_norm(grp, feats, norm))
    return out


def fit_family(train: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    family = str(spec.get("family") or "")
    if family == FAMILY_LOGIT:
        return fit_logit(train, spec)
    if family == FAMILY_RF:
        return fit_rf(train, spec)
    return {"kind": "fail", "family": family, "features": list(spec.get("features") or [])}


def score_family(rows: list[dict[str, Any]], fit: dict[str, Any]) -> list[dict[str, Any]]:
    feats = list(fit.get("features") or [])
    norm = str(fit.get("normalization") or "none")
    kind = str(fit.get("kind") or "fail")
    out: list[dict[str, Any]] = []
    for grp in cohorts(rows).values():
        normed = apply_norm(grp, feats, norm) if kind != "fail" else [dict(r) for r in grp]
        if kind == "fail":
            for r in normed:
                rec = dict(r)
                rec[JOINT_SCORE_KEY] = None
                rec["P_WIN"] = None
                rec["P_LOSS"] = None
                rec["P_NEUTRAL"] = None
                out.append(rec)
            continue
        X, ok = _feat_matrix(normed, feats)
        if kind == "logit":
            mean = np.asarray(fit.get("scaler_mean") or [], dtype=float)
            scale = np.asarray(fit.get("scaler_scale") or [], dtype=float)
            scale = np.where(scale == 0, 1.0, scale)
            Xt = (X - mean) / scale if mean.size else X
        else:
            Xt = X
        joint = np.full(len(normed), np.nan, dtype=float)
        p_win = np.full(len(normed), np.nan, dtype=float)
        p_loss = np.full(len(normed), np.nan, dtype=float)
        p_neu = np.full(len(normed), np.nan, dtype=float)
        if int(ok.sum()) > 0:
            j, w, lo, n = _proba_joint(fit.get("model"), Xt[ok])
            joint[ok] = j
            p_win[ok] = w
            p_loss[ok] = lo
            p_neu[ok] = n
        for r, sc, pw, pl, pn, g in zip(normed, joint, p_win, p_loss, p_neu, ok):
            rec = dict(r)
            rec[JOINT_SCORE_KEY] = float(sc) if bool(g) and sc == sc else None
            rec["P_WIN"] = float(pw) if bool(g) and pw == pw else None
            rec["P_LOSS"] = float(pl) if bool(g) and pl == pl else None
            rec["P_NEUTRAL"] = float(pn) if bool(g) and pn == pn else None
            out.append(rec)
    return out

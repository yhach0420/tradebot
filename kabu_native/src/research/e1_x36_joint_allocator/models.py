"""Allocator families A0–A4: low-complexity regularized linear models."""
from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from . import FEATURE_SETS, MAX_ACTIVE_FEATURES, REG_GRID_LOG, REG_GRID_RIDGE


def _matrix(rows: list[dict], features: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Return X and mask of rows with all features finite."""
    X = []
    ok = []
    for r in rows:
        vec = []
        good = True
        for f in features:
            v = r.get(f)
            if v is None or not np.isfinite(v):
                good = False
                break
            vec.append(float(v))
        ok.append(good)
        X.append(vec if good else [0.0] * len(features))
    return np.asarray(X, dtype=float), np.asarray(ok, dtype=bool)


def fit_allocator(
    train: list[dict],
    *,
    family: str,
    feature_set: str,
    reg: float,
) -> dict[str, Any]:
    feats = FEATURE_SETS[feature_set]
    assert len(feats) <= MAX_ACTIVE_FEATURES
    X, ok = _matrix(train, feats)
    if family == "A0_ASC":
        return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "asc", "features": feats}

    if ok.sum() < 40:
        return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "fail", "features": feats}

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X[ok])

    if family == "A1_FILL":
        y = np.asarray([int(r["FILL_1S"]) for r, m in zip(train, ok) if m], dtype=int)
        if len(np.unique(y)) < 2:
            return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "fail", "features": feats}
        clf = LogisticRegression(C=float(reg), max_iter=500, solver="lbfgs")
        clf.fit(Xs, y)
        return {
            "family": family, "feature_set": feature_set, "reg": reg, "kind": "fill",
            "features": feats, "scaler": scaler, "model": clf,
        }

    if family == "A2_EDGE":
        # train only on filled
        idx = [i for i, (r, m) in enumerate(zip(train, ok)) if m and r.get("FILL_1S") == 1 and r.get("FIXED600_NET_BPS") is not None]
        if len(idx) < 30:
            return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "fail", "features": feats}
        Xf = scaler.fit_transform(X[idx])
        y = np.asarray([float(train[i]["FIXED600_NET_BPS"]) for i in idx], dtype=float)
        reg_m = Ridge(alpha=float(reg))
        reg_m.fit(Xf, y)
        return {
            "family": family, "feature_set": feature_set, "reg": reg, "kind": "edge",
            "features": feats, "scaler": scaler, "model": reg_m,
        }

    if family == "A3_EOV":
        # fit fill + edge separately
        yf = np.asarray([int(r["FILL_1S"]) for r, m in zip(train, ok) if m], dtype=int)
        if len(np.unique(yf)) < 2:
            return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "fail", "features": feats}
        clf = LogisticRegression(C=float(reg), max_iter=500, solver="lbfgs")
        clf.fit(Xs, yf)
        idx = [i for i, (r, m) in enumerate(zip(train, ok)) if m and r.get("FILL_1S") == 1 and r.get("FIXED600_NET_BPS") is not None]
        if len(idx) < 30:
            return {"family": family, "feature_set": feature_set, "reg": reg, "kind": "fail", "features": feats}
        scaler2 = StandardScaler()
        Xf = scaler2.fit_transform(X[idx])
        ye = np.asarray([float(train[i]["FIXED600_NET_BPS"]) for i in idx], dtype=float)
        edge = Ridge(alpha=float(reg))
        edge.fit(Xf, ye)
        return {
            "family": family, "feature_set": feature_set, "reg": reg, "kind": "eov",
            "features": feats, "scaler": scaler, "fill_model": clf,
            "edge_scaler": scaler2, "edge_model": edge,
        }

    if family == "A4_DIRECT":
        y = np.asarray([float(r.get("OPPORTUNITY_VALUE_600") or 0.0) for r, m in zip(train, ok) if m], dtype=float)
        reg_m = Ridge(alpha=float(reg))
        reg_m.fit(Xs, y)
        return {
            "family": family, "feature_set": feature_set, "reg": reg, "kind": "direct",
            "features": feats, "scaler": scaler, "model": reg_m,
        }

    raise ValueError(family)


def score_fn_from_fit(fit: dict[str, Any]) -> Optional[Callable[[dict], float]]:
    kind = fit.get("kind")
    if kind == "asc":
        return None  # use neutral ASC
    if kind == "fail":
        return lambda e: float("-inf")

    feats = fit["features"]

    def _x(e: dict) -> Optional[np.ndarray]:
        vec = []
        for f in feats:
            v = e.get(f)
            if v is None or not np.isfinite(v):
                return None
            vec.append(float(v))
        return np.asarray(vec, dtype=float).reshape(1, -1)

    if kind == "fill":
        def _s(e: dict) -> float:
            x = _x(e)
            if x is None:
                return float("-inf")
            xs = fit["scaler"].transform(x)
            return float(fit["model"].predict_proba(xs)[0, 1])
        return _s

    if kind == "edge":
        def _s(e: dict) -> float:
            x = _x(e)
            if x is None:
                return float("-inf")
            xs = fit["scaler"].transform(x)
            return float(fit["model"].predict(xs)[0])
        return _s

    if kind == "eov":
        def _s(e: dict) -> float:
            x = _x(e)
            if x is None:
                return float("-inf")
            xs = fit["scaler"].transform(x)
            p = float(fit["fill_model"].predict_proba(xs)[0, 1])
            xe = fit["edge_scaler"].transform(x)
            er = float(fit["edge_model"].predict(xe)[0])
            return p * er
        return _s

    if kind == "direct":
        def _s(e: dict) -> float:
            x = _x(e)
            if x is None:
                return float("-inf")
            xs = fit["scaler"].transform(x)
            return float(fit["model"].predict(xs)[0])
        return _s

    raise ValueError(kind)


def candidate_specs() -> list[dict[str, Any]]:
    specs = [{"family": "A0_ASC", "feature_set": "EXEC", "reg": 1.0}]
    for fam in ("A1_FILL", "A2_EDGE", "A3_EOV", "A4_DIRECT"):
        regs = REG_GRID_LOG if fam == "A1_FILL" else REG_GRID_RIDGE
        for fs in FEATURE_SETS:
            for reg in regs:
                specs.append({"family": fam, "feature_set": fs, "reg": float(reg)})
    return specs


def fit_spec(train: list[dict], spec: dict) -> dict[str, Any]:
    return fit_allocator(
        train,
        family=spec["family"],
        feature_set=spec["feature_set"],
        reg=float(spec["reg"]),
    )

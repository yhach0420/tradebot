"""Precommitted shallow tree + logistic. No CV. No depth/cutoff search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_path_state_discrimination_v1 import (
    EVAL_BLOCKS,
    LOGISTIC_C,
    LOGISTIC_PENALTY,
    MIN_AUC,
    MIN_LIFT,
    PRED_CUTOFF,
    TREE_CRITERION,
    TREE_MAX_DEPTH,
    TREE_MIN_SAMPLES_LEAF,
    TREE_RANDOM_STATE,
    TRAIN_BLOCK,
)
from research.native_path_state_discrimination_v1.features import FEATURE_SETS, PRIMARY_FEATURES


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def contrast_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("in_contrast") and r.get("y") in (0, 1)]


def matrix(rows: list[dict[str, Any]], names: tuple[str, ...], *, med: dict[str, float] | None) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    if med is None:
        med = {}
        for n in names:
            xs = [float(r.get("state", {}).get(n)) for r in rows if _finite((r.get("state") or {}).get(n))]
            med[n] = float(np.median(xs)) if xs else 0.0
    X = np.zeros((len(rows), len(names)), dtype=float)
    y = np.zeros(len(rows), dtype=int)
    for i, r in enumerate(rows):
        st = r.get("state") or {}
        for j, n in enumerate(names):
            v = st.get(n)
            X[i, j] = float(v) if _finite(v) else float(med[n])
        y[i] = int(r["y"])
    return X, y, med


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    if y.size < 20 or len(set(int(v) for v in y)) < 2:
        return None
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y, p))
    except Exception:
        return None


def _metrics(y: np.ndarray, p: np.ndarray, *, cutoff: float = PRED_CUTOFF) -> dict[str, Any]:
    hat = (p >= float(cutoff)).astype(int)
    base = float(np.mean(y)) if y.size else None
    pos = y[hat == 1]
    lift = None if base is None or pos.size == 0 else float(np.mean(pos) - base)
    tp = int(np.sum((hat == 1) & (y == 1)))
    fp = int(np.sum((hat == 1) & (y == 0)))
    prec = (tp / (tp + fp)) if (tp + fp) else None
    return {
        "n": int(y.size),
        "base_favorable_rate": base,
        "auc": _auc(y, p),
        "pred_pos_n": int(np.sum(hat)),
        "pred_pos_rate": float(np.mean(hat)) if hat.size else None,
        "precision_at_cutoff": prec,
        "lift_at_cutoff": lift,
        "cutoff": float(cutoff),
        "mean_prob": float(np.mean(p)) if p.size else None,
    }


def fit_tree(X: np.ndarray, y: np.ndarray, names: tuple[str, ...]) -> dict[str, Any]:
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
    if X.shape[0] < TREE_MIN_SAMPLES_LEAF * 2:
        return {"ok": False, "reason": "n"}
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH,
        min_samples_leaf=TREE_MIN_SAMPLES_LEAF,
        criterion=TREE_CRITERION,
        random_state=TREE_RANDOM_STATE,
        class_weight=None,
    )
    clf.fit(X, y)
    return {
        "ok": True,
        "model": clf,
        "rules_text": export_text(clf, feature_names=list(names), decimals=4),
        "importances": {names[i]: float(clf.feature_importances_[i]) for i in range(len(names))},
        "max_depth": TREE_MAX_DEPTH,
        "min_samples_leaf": TREE_MIN_SAMPLES_LEAF,
        "criterion": TREE_CRITERION,
        "random_state": TREE_RANDOM_STATE,
        "class_weight": None,
        "cutoff": PRED_CUTOFF,
    }


def fit_logistic(X: np.ndarray, y: np.ndarray, names: tuple[str, ...]) -> dict[str, Any]:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
    if X.shape[0] < 80 or len(set(int(v) for v in y)) < 2:
        return {"ok": False, "reason": "n"}
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    clf = LogisticRegression(
        C=LOGISTIC_C,
        solver="lbfgs",
        max_iter=1000,
        class_weight=None,
        random_state=TREE_RANDOM_STATE,
    )
    clf.fit(Xs, y)
    coef = {names[i]: float(clf.coef_[0][i]) for i in range(len(names))}
    return {"ok": True, "model": clf, "scaler": scaler, "coef": coef, "C": LOGISTIC_C, "penalty": LOGISTIC_PENALTY}


def predict_tree(pack: dict[str, Any], X: np.ndarray) -> np.ndarray:
    if not pack.get("ok"):
        return np.full(X.shape[0], 0.5)
    return pack["model"].predict_proba(X)[:, 1]


def predict_logistic(pack: dict[str, Any], X: np.ndarray) -> np.ndarray:
    if not pack.get("ok"):
        return np.full(X.shape[0], 0.5)
    Xs = pack["scaler"].transform(X)
    return pack["model"].predict_proba(Xs)[:, 1]


def top_family(importances: dict[str, float]) -> str | None:
    if not importances:
        return None
    fam = {
        "return": ("ret_1m", "ret_3m", "ret_5m", "pullback", "pullback_depth", "mins_since_impulse"),
        "group": tuple(k for k in importances if k.startswith("g_")),
        "sector": ("sector_rel_sess", "market_rel_sess", "sector_rank_pct", "leading", "lagging", "sector_breadth_pos"),
        "activity": ("vol_rel20", "va_rel20", "rng_rel20", "rvol_1m", "vol_expand", "va_expand", "range_expand", "compression"),
        "vwap": ("dist_vwap", "vwap_reclaim", "above_vwap"),
        "level": ("dist_20high", "dist_20low", "opening_gap", "mins_from_open"),
    }
    scores: dict[str, float] = defaultdict(float)
    for fam_name, keys in fam.items():
        for k in keys:
            scores[fam_name] += float(importances.get(k) or 0)
    if not scores:
        return None
    return max(scores.items(), key=lambda kv: kv[1])[0]


def prequential(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in contrast_rows(rows):
        by_block[str(r.get("block") or "")].append(r)
    order = [TRAIN_BLOCK, *EVAL_BLOCKS]
    steps = []
    trees = {}
    logs = {}
    abl = {}
    for i, eval_b in enumerate(order):
        if eval_b == TRAIN_BLOCK:
            train = by_block.get(TRAIN_BLOCK) or []
            eval_rows = train
            role = "characterization"
        else:
            train_blocks = order[: order.index(eval_b)]
            train = [r for b in train_blocks for r in (by_block.get(b) or [])]
            eval_rows = by_block.get(eval_b) or []
            role = "eval"
        Xtr, ytr, med = matrix(train, PRIMARY_FEATURES, med=None)
        tree = fit_tree(Xtr, ytr, PRIMARY_FEATURES)
        logi = fit_logistic(Xtr, ytr, PRIMARY_FEATURES)
        Xev, yev, _ = matrix(eval_rows, PRIMARY_FEATURES, med=med)
        pt = predict_tree(tree, Xev)
        pl = predict_logistic(logi, Xev)
        tree_m = _metrics(yev, pt)
        log_m = _metrics(yev, pl)
        nested = []
        for set_name, names in FEATURE_SETS.items():
            Xt, yt, md = matrix(train, names, med=None)
            lg = fit_logistic(Xt, yt, names)
            Xe, ye, _ = matrix(eval_rows, names, med=md)
            nested.append({"set": set_name, **_metrics(ye, predict_logistic(lg, Xe)), "ok": lg.get("ok")})
        step = {
            "block": eval_b,
            "role": role,
            "train_n": len(train),
            "eval_n": len(eval_rows),
            "tree": {k: v for k, v in tree.items() if k != "model"},
            "tree_metrics": tree_m,
            "logistic_metrics": log_m,
            "logistic_coef_top": sorted(
                ((k, v) for k, v in dict(logi.get("coef") or {}).items()),
                key=lambda kv: abs(kv[1]),
                reverse=True,
            )[:12],
            "nested_logistic": nested,
            "top_family": top_family(dict(tree.get("importances") or {})),
        }
        steps.append(step)
        trees[eval_b] = {"fit": tree, "med": med, "train_blocks": order[: max(1, order.index(eval_b))]}
        logs[eval_b] = logi
        abl[eval_b] = nested
    d2 = next((s for s in steps if s["block"] == "D2"), {})
    d3 = next((s for s in steps if s["block"] == "D3"), {})
    d4 = next((s for s in steps if s["block"] == "D4"), {})
    core_rows = [r for r in contrast_rows(rows) if str(r.get("block")) in {"D2", "D3"}]
    # score D2+D3 with D1-trained tree (first OOS model, not refit on D2)
    d1_tree = trees.get(TRAIN_BLOCK) or trees.get("D2")
    d23 = {}
    if d1_tree and (d1_tree.get("fit") or {}).get("ok"):
        Xc, yc, _ = matrix(core_rows, PRIMARY_FEATURES, med=d1_tree.get("med"))
        d23 = _metrics(yc, predict_tree(d1_tree["fit"], Xc))
        d23["scored_with"] = "D1_tree"
    inc = {}
    for blk, nested in abl.items():
        by = {r["set"]: r.get("auc") for r in nested}
        inc[blk] = {
            "group": None if by.get("F_BASE") is None or by.get("F_GROUP") is None else float(by["F_GROUP"]) - float(by["F_BASE"]),
            "sector": None if by.get("F_GROUP") is None or by.get("F_SECTOR") is None else float(by["F_SECTOR"]) - float(by["F_GROUP"]),
            "activity": None if by.get("F_SECTOR") is None or by.get("F_ACTIVITY") is None else float(by["F_ACTIVITY"]) - float(by["F_SECTOR"]),
            "vwap": None if by.get("F_ACTIVITY") is None or by.get("F_VWAP") is None else float(by["F_VWAP"]) - float(by["F_ACTIVITY"]),
        }
    return {
        "ok": bool(d2.get("tree", {}).get("ok")),
        "spec": {
            "tree_max_depth": TREE_MAX_DEPTH,
            "min_samples_leaf": TREE_MIN_SAMPLES_LEAF,
            "criterion": TREE_CRITERION,
            "logistic_C": LOGISTIC_C,
            "cutoff": PRED_CUTOFF,
            "random_cv": False,
            "primary_features": list(PRIMARY_FEATURES),
        },
        "steps": [{k: v for k, v in s.items()} for s in steps],
        "D1": next((s for s in steps if s["block"] == "D1"), {}),
        "D2": d2,
        "D3": d3,
        "D4": d4,
        "D2_D3": d23,
        "incremental_auc": inc,
        "trees": trees,
        "min_auc": MIN_AUC,
        "min_lift": MIN_LIFT,
    }

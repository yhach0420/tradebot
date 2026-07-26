"""Explainable identification models — TRAIN-fit only, no AutoML."""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from research.upward_edge_identification_audit.constants import COST_BPS, HYPOTHESES, MODELS
from research.upward_edge_identification_audit.features import features_for_groups
from research.upward_edge_identification_audit.labels import label_summary
from research.upward_edge_identification_audit.samples import Sample


def _y_up(s: Sample, barrier: str) -> Optional[int]:
    lab = s.labels.get(barrier)
    if lab is None:
        return None
    if lab.first_result == "UP_FIRST":
        return 1
    if lab.first_result == "DOWN_FIRST":
        return 0
    return None  # exclude NEITHER/etc from binary AUC


def _feat_matrix(samples: Sequence[Sample], groups: Sequence[str], keys: list[str] | None = None):
    rows = []
    for s in samples:
        f = features_for_groups(s.features, groups) if groups else {}
        rows.append(f)
    if keys is None:
        keyset = set()
        for r in rows:
            keyset.update(r.keys())
        keys = sorted(keyset)
    # TRAIN medians for imputation — caller should pass train medians
    return rows, keys


def _impute(rows: list[dict], keys: list[str], medians: dict[str, float]) -> list[list[float]]:
    X = []
    for r in rows:
        vec = []
        for k in keys:
            v = r.get(k)
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                v = medians.get(k, 0.0)
            vec.append(float(v))
        X.append(vec)
    return X


def _train_medians(rows: list[dict], keys: list[str]) -> dict[str, float]:
    cols: dict[str, list[float]] = {k: [] for k in keys}
    for r in rows:
        for k in keys:
            v = r.get(k)
            if v is not None and isinstance(v, (int, float)) and not math.isnan(float(v)) and not math.isinf(float(v)):
                cols[k].append(float(v))
    out = {}
    for k, vals in cols.items():
        if not vals:
            out[k] = 0.0
        else:
            vals = sorted(vals)
            out[k] = vals[len(vals) // 2]
    return out


def _standardize_fit(X: list[list[float]]) -> tuple[list[float], list[float]]:
    if not X:
        return [], []
    p = len(X[0])
    means = []
    stds = []
    for j in range(p):
        col = [row[j] for row in X]
        m = sum(col) / len(col)
        var = sum((x - m) ** 2 for x in col) / max(1, len(col) - 1)
        s = math.sqrt(var) if var > 1e-12 else 1.0
        means.append(m)
        stds.append(s)
    return means, stds


def _apply_std(X: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    return [[(row[j] - means[j]) / stds[j] for j in range(len(means))] for row in X]


def _sigmoid(z: float) -> float:
    if z >= 30:
        return 1.0
    if z <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_logit(X: list[list[float]], y: list[int], lr: float = 0.15, epochs: int = 40, l2: float = 0.5):
    if not X:
        return [0.0], 0.0
    # Cap fit size (even stride) — day-causal order preserved, not random split
    if len(X) > 12000:
        step = max(1, len(X) // 12000)
        X = X[::step]
        y = y[::step]
    p = len(X[0])
    w = [0.0] * p
    b = 0.0
    n = len(X)
    for _ in range(epochs):
        gw = [0.0] * p
        gb = 0.0
        for i in range(n):
            z = b + sum(w[j] * X[i][j] for j in range(p))
            p_i = _sigmoid(z)
            err = p_i - y[i]
            for j in range(p):
                gw[j] += err * X[i][j]
            gb += err
        for j in range(p):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * (gb / n)
    return w, b


def predict_proba(X: list[list[float]], w: list[float], b: float) -> list[float]:
    return [_sigmoid(b + sum(w[j] * row[j] for j in range(len(w)))) for row in X]


def roc_auc(y: list[int], p: list[float]) -> Optional[float]:
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    # Mann–Whitney via average ranks (O(n log n))
    order = sorted(range(len(p)), key=lambda i: p[i])
    ranks = [0.0] * len(p)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and p[order[j + 1]] == p[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(ranks[i] for i in range(len(y)) if y[i] == 1)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pr_auc(y: list[int], p: list[float]) -> Optional[float]:
    pairs = sorted(zip(p, y), key=lambda x: x[0], reverse=True)
    tp = fp = 0
    pos = sum(y)
    if pos == 0:
        return None
    prev_r = prev_p = 0.0
    area = 0.0
    for i, (_, yi) in enumerate(pairs):
        if yi == 1:
            tp += 1
        else:
            fp += 1
        recall = tp / pos
        prec = tp / (tp + fp)
        area += (recall - prev_r) * (prec + prev_p) / 2
        prev_r, prev_p = recall, prec
    return area


def brier(y: list[int], p: list[float]) -> float:
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / max(1, len(y))


def log_loss(y: list[int], p: list[float]) -> float:
    eps = 1e-9
    return -sum(yi * math.log(max(eps, pi)) + (1 - yi) * math.log(max(eps, 1 - pi)) for yi, pi in zip(y, p)) / max(1, len(y))


def top_decile_lift(y: list[int], p: list[float]) -> Optional[float]:
    if not y:
        return None
    base = sum(y) / len(y)
    if base <= 0:
        return None
    pairs = sorted(zip(p, y), key=lambda x: x[0], reverse=True)
    k = max(1, len(pairs) // 10)
    top = sum(yi for _, yi in pairs[:k]) / k
    return top / base


def top_quintile_lift(y: list[int], p: list[float]) -> Optional[float]:
    if not y:
        return None
    base = sum(y) / len(y)
    if base <= 0:
        return None
    pairs = sorted(zip(p, y), key=lambda x: x[0], reverse=True)
    k = max(1, len(pairs) // 5)
    top = sum(yi for _, yi in pairs[:k]) / k
    return top / base


def population_metrics(samples: Sequence[Sample], barrier: str) -> dict[str, Any]:
    labs = [s.labels[barrier] for s in samples if barrier in s.labels]
    return label_summary(labs)


def evaluate_scored(
    samples: Sequence[Sample],
    barrier: str,
    scores: list[float],
    binary_mask: list[bool],
) -> dict[str, Any]:
    y = []
    p = []
    kept = []
    for s, sc, m in zip(samples, scores, binary_mask):
        if not m:
            continue
        yi = _y_up(s, barrier)
        if yi is None:
            continue
        y.append(yi)
        p.append(sc)
        kept.append(s)
    base = population_metrics(samples, barrier)
    if len(set(y)) < 2:
        return {**base, "roc_auc": None, "pr_auc": None, "n_binary": len(y), "note": "insufficient_class_balance"}
    # top decile among scored binary
    pairs = sorted(zip(p, kept), key=lambda x: x[0], reverse=True)
    k = max(1, len(pairs) // 10)
    top_s = [s for _, s in pairs[:k]]
    top_m = population_metrics(top_s, barrier)
    return {
        **base,
        "n_binary": len(y),
        "roc_auc": roc_auc(y, p),
        "pr_auc": pr_auc(y, p),
        "brier": brier(y, p),
        "log_loss": log_loss(y, p),
        "top_decile_lift": top_decile_lift(y, p),
        "top_quintile_lift": top_quintile_lift(y, p),
        "top_decile_up_rate": top_m.get("UP_FIRST_rate"),
        "top_decile_cost_adj": top_m.get("avg_cost_adj_bps"),
        "top_decile_mfe_mae": top_m.get("mfe_mae_ratio"),
        "top_decile_up_down": top_m.get("up_down_ratio"),
    }


def fit_group_model(
    train: Sequence[Sample],
    test: Sequence[Sample],
    groups: Sequence[str],
    barrier: str,
) -> dict[str, Any]:
    # M0: constant score = base rate
    if not groups:
        base = sum(1 for s in train if _y_up(s, barrier) == 1) / max(1, sum(1 for s in train if _y_up(s, barrier) is not None))
        scores = [base] * len(test)
        mask = [True] * len(test)
        return {
            "groups": [],
            "train": evaluate_scored(train, barrier, [base] * len(train), [True] * len(train)),
            "test": evaluate_scored(test, barrier, scores, mask),
            "keys": [],
        }

    tr_rows, keys = _feat_matrix(train, groups)
    te_rows, _ = _feat_matrix(test, groups, keys)
    med = _train_medians(tr_rows, keys)
    Xtr = _impute(tr_rows, keys, med)
    Xte = _impute(te_rows, keys, med)
    ytr = []
    Xtr_f = []
    for s, row in zip(train, Xtr):
        yi = _y_up(s, barrier)
        if yi is None:
            continue
        ytr.append(yi)
        Xtr_f.append(row)
    if len(set(ytr)) < 2 or len(Xtr_f) < 30:
        return {"groups": list(groups), "train": {}, "test": {}, "keys": keys, "note": "fit_failed"}
    means, stds = _standardize_fit(Xtr_f)
    Xtr_s = _apply_std(Xtr_f, means, stds)
    w, b = fit_logit(Xtr_s, ytr)
    # train scores
    tr_scores_all = []
    tr_mask = []
    idx_map = {id(s): i for i, s in enumerate(train)}
    # rebuild full train matrix
    Xtr_all_s = _apply_std(Xtr, means, stds)
    ptr = predict_proba(Xtr_all_s, w, b)
    for s, sc in zip(train, ptr):
        tr_scores_all.append(sc)
        tr_mask.append(_y_up(s, barrier) is not None)
    Xte_s = _apply_std(Xte, means, stds)
    pte = predict_proba(Xte_s, w, b)
    te_mask = [_y_up(s, barrier) is not None for s in test]
    return {
        "groups": list(groups),
        "keys": keys,
        "n_features": len(keys),
        "train": evaluate_scored(train, barrier, tr_scores_all, tr_mask),
        "test": evaluate_scored(test, barrier, pte, te_mask),
        "weights_top": sorted(
            [{"feature": keys[j], "weight": w[j]} for j in range(len(keys))],
            key=lambda x: abs(x["weight"]), reverse=True,
        )[:15],
    }


def univariate_bins(train: Sequence[Sample], barrier: str, feature: str, n_bins: int = 5) -> list[dict[str, Any]]:
    vals = []
    for s in train:
        v = s.features.get(feature)
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        yi = _y_up(s, barrier)
        lab = s.labels.get(barrier)
        if lab is None:
            continue
        vals.append((float(v), s))
    if len(vals) < 50:
        return []
    vals.sort(key=lambda x: x[0])
    # TRAIN quantile edges
    edges = []
    for b in range(n_bins + 1):
        idx = min(len(vals) - 1, int(b * (len(vals) - 1) / n_bins))
        edges.append(vals[idx][0])
    rows = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        bucket = [s for v, s in vals if (v >= lo and (v <= hi if b == n_bins - 1 else v < hi)) or (b == n_bins - 1 and v <= hi)]
        # fix empty
        if b < n_bins - 1:
            bucket = [s for v, s in vals if lo <= v < hi] if b > 0 or True else bucket
            if b == 0:
                bucket = [s for v, s in vals if v <= hi and v >= lo]
                # simpler: assign by rank
        start = int(b * len(vals) / n_bins)
        end = int((b + 1) * len(vals) / n_bins)
        bucket = [s for _, s in vals[start:end]]
        m = population_metrics(bucket, barrier)
        days = len({s.day for s in bucket})
        syms = len({s.symbol for s in bucket})
        rows.append({"feature": feature, "bin": b, "lo": vals[start][0], "hi": vals[end - 1][0], "days": days, "symbols": syms, **m})
    return rows


def pick_univariate_candidates(train: Sequence[Sample], barrier: str, max_features: int = 40) -> list[dict[str, Any]]:
    # score features by |top_bin_up - bottom_bin_up|
    keys = sorted({k for s in train for k in s.features.keys()})
    scored = []
    for k in keys[:200]:
        bins = univariate_bins(train, barrier, k)
        if len(bins) < 3:
            continue
        sep = abs((bins[-1].get("UP_FIRST_rate") or 0) - (bins[0].get("UP_FIRST_rate") or 0))
        scored.append({"feature": k, "separation": sep, "bins": bins})
    scored.sort(key=lambda x: x["separation"], reverse=True)
    return scored[:max_features]


def run_models(
    train: Sequence[Sample],
    val: Sequence[Sample],
    barrier: str,
) -> dict[str, Any]:
    model_out = {}
    for mid, groups in MODELS.items():
        model_out[mid] = fit_group_model(train, val if val else train, groups, barrier)
    hyp_out = {}
    for hid, groups in HYPOTHESES.items():
        hyp_out[hid] = fit_group_model(train, val if val else train, groups, barrier)
    return {"models": model_out, "hypotheses": hyp_out}


def daily_auc(samples: Sequence[Sample], scores: list[float], barrier: str) -> dict[str, Optional[float]]:
    by: dict[str, list[tuple[int, float]]] = {}
    for s, sc in zip(samples, scores):
        yi = _y_up(s, barrier)
        if yi is None:
            continue
        by.setdefault(s.day, []).append((yi, sc))
    out = {}
    for d, rows in by.items():
        y = [a for a, _ in rows]
        p = [b for _, b in rows]
        out[d] = roc_auc(y, p) if len(set(y)) > 1 else None
    return out

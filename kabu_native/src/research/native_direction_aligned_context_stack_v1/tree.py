"""D1-only shallow tree. Family-level tertile stability. No hyperparameter search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np

from research.native_direction_aligned_context_stack_v1 import (
    FAMILIES,
    FEATURE_NAMES,
    MAX_FAVORABLE_RULES,
    TRAIN_BLOCK,
    TREE_CRITERION,
    TREE_MAX_DEPTH,
    TREE_MIN_LEAF_ABS,
    TREE_MIN_LEAF_FRAC,
    TREE_RANDOM_STATE,
)

ZERO_FILL = {"ahead_sr_distance_atr", "behind_sr_distance_atr"}


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def min_leaf_n(d1_n: int) -> int:
    return int(max(int(TREE_MIN_LEAF_ABS), int(np.ceil(float(TREE_MIN_LEAF_FRAC) * float(max(d1_n, 0))))))


def fill_value(row: dict[str, Any], name: str, med: dict[str, float]) -> float:
    v = row.get(name)
    if _finite(v):
        return float(v)
    if name in ZERO_FILL:
        return 0.0
    return float(med.get(name, 0.0))


def d1_medians(rows: list[dict[str, Any]], names: tuple[str, ...] = FEATURE_NAMES) -> dict[str, float]:
    med: dict[str, float] = {}
    for n in names:
        if n in ZERO_FILL:
            med[n] = 0.0
            continue
        xs = [float(r.get(n)) for r in rows if _finite(r.get(n))]
        med[n] = float(np.median(xs)) if xs else 0.0
    return med


def matrix(rows: list[dict[str, Any]], names: tuple[str, ...], med: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    X = np.zeros((len(rows), len(names)), dtype=float)
    y = np.zeros(len(rows), dtype=int)
    for i, r in enumerate(rows):
        for j, n in enumerate(names):
            X[i, j] = fill_value(r, n, med)
        y[i] = int(r.get("y_p40") or 0)
    return X, y


def used_features(clf: Any, names: tuple[str, ...]) -> list[str]:
    out = []
    for i in clf.tree_.feature:
        if int(i) >= 0:
            name = names[int(i)]
            if name not in out:
                out.append(name)
    return out


def first_split(clf: Any, names: tuple[str, ...]) -> str | None:
    f = int(clf.tree_.feature[0])
    return None if f < 0 else names[f]


def family_of(name: str) -> str | None:
    for fam, keys in FAMILIES.items():
        if name in keys:
            return fam
    return None


def families_used(feats: list[str]) -> list[str]:
    out = []
    for n in feats:
        f = family_of(n)
        if f and f not in out:
            out.append(f)
    return out


def leaf_paths(clf: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    tree = clf.tree_
    out: list[dict[str, Any]] = []

    def walk(node: int, path: list[tuple[str, str, float]]) -> None:
        if tree.children_left[node] == tree.children_right[node]:
            val = tree.value[node][0]
            n = int(tree.n_node_samples[node])
            p1 = float(val[1] / val.sum()) if val.sum() else 0.0
            out.append({"leaf": int(node), "n_train": n, "p_favorable": p1, "predicates": list(path)})
            return
        name = names[int(tree.feature[node])]
        t = float(tree.threshold[node])
        walk(int(tree.children_left[node]), path + [(name, "<=", t)])
        walk(int(tree.children_right[node]), path + [(name, ">", t)])

    walk(0, [])
    return out


def apply_pred(row: dict[str, Any], pred: tuple[str, str, float], med: dict[str, float]) -> bool:
    name, op, thr = pred
    v = fill_value(row, name, med)
    return v <= thr if op == "<=" else v > thr


def match_rule(row: dict[str, Any], predicates: list[tuple[str, str, float]], med: dict[str, float]) -> bool:
    return all(apply_pred(row, p, med) for p in predicates)


def pretty(predicates: list[tuple[str, str, float]]) -> str:
    if not predicates:
        return "(root)"
    return " AND ".join(f"{a} {b} {c:.6g}" for a, b, c in predicates)


def fit_tree(X: np.ndarray, y: np.ndarray, names: tuple[str, ...], *, min_leaf: int) -> dict[str, Any]:
    try:
        from sklearn.tree import DecisionTreeClassifier, export_text
    except Exception as exc:
        return {"ok": False, "reason": f"sklearn:{exc}"}
    if X.shape[0] < int(min_leaf) * 2:
        return {"ok": False, "reason": "n"}
    clf = DecisionTreeClassifier(
        max_depth=TREE_MAX_DEPTH,
        min_samples_leaf=int(min_leaf),
        criterion=TREE_CRITERION,
        random_state=TREE_RANDOM_STATE,
        class_weight=None,
    )
    clf.fit(X, y)
    return {
        "ok": True,
        "model": clf,
        "rules_text": export_text(clf, feature_names=list(names), decimals=6),
        "importances": {names[i]: float(clf.feature_importances_[i]) for i in range(len(names))},
        "used_features": used_features(clf, names),
        "used_families": families_used(used_features(clf, names)),
        "first_split": first_split(clf, names),
        "max_depth": TREE_MAX_DEPTH,
        "min_samples_leaf": int(min_leaf),
        "criterion": TREE_CRITERION,
        "random_state": TREE_RANDOM_STATE,
        "class_weight": None,
    }


def d1_family_stability(d1: list[dict[str, Any]], med: dict[str, float], min_leaf: int) -> dict[str, Any]:
    dates = sorted({str(r.get("date") or "") for r in d1 if r.get("date")})
    if len(dates) < 6:
        return {"ok": False, "reason": "few_d1_dates", "unstable": True, "stable_families": []}
    cuts = [0, len(dates) // 3, 2 * len(dates) // 3, len(dates)]
    folds = []
    counts = {fam: 0 for fam in FAMILIES}
    for k in range(3):
        keep = set(dates[cuts[k] : cuts[k + 1]])
        rows = [r for r in d1 if str(r.get("date") or "") in keep]
        Xs, ys = matrix(rows, FEATURE_NAMES, med)
        fit = fit_tree(Xs, ys, FEATURE_NAMES, min_leaf=min_leaf)
        fams = set(fit.get("used_families") or [])
        for fam in fams:
            counts[fam] += 1
        folds.append(
            {
                "fold": k + 1,
                "date_first": dates[cuts[k]] if keep else None,
                "date_last": dates[cuts[k + 1] - 1] if keep else None,
                "n": len(rows),
                "ok": fit.get("ok"),
                "used_features": fit.get("used_features"),
                "used_families": sorted(fams),
                "first_split": fit.get("first_split"),
                "rules_text": fit.get("rules_text") if fit.get("ok") else None,
            }
        )
    stable = [fam for fam, n in counts.items() if n >= 2]
    return {
        "ok": True,
        "n_dates": len(dates),
        "folds": folds,
        "family_fold_counts": counts,
        "stable_families": stable,
        "unstable": len(stable) == 0,
        "rule": "family is D1-stable iff used in at least 2/3 chronological tertile trees; identical thresholds not required",
        "empty_topology_intersection_not_called_stable": True,
    }


def tree_sha256(*, rules_text: str, med: dict[str, float], min_leaf: int, predicates: list[Any]) -> str:
    h = hashlib.sha256()
    h.update(str(rules_text or "").encode("utf-8"))
    h.update(json.dumps(med, sort_keys=True, default=str).encode("utf-8"))
    h.update(str(min_leaf).encode("utf-8"))
    h.update(json.dumps(predicates, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def extract_rules(clf: Any, d1: list[dict[str, Any]], med: dict[str, float], min_leaf: int) -> list[dict[str, Any]]:
    leaves = leaf_paths(clf, FEATURE_NAMES)
    base = float(np.mean([int(r.get("y_p40") or 0) for r in d1])) if d1 else 0.0
    scored = []
    for leaf in leaves:
        if int(leaf["n_train"]) < int(min_leaf):
            continue
        p = float(leaf["p_favorable"])
        if p <= base:
            continue
        preds = [tuple(p_) for p_ in leaf["predicates"]]
        fams = sorted({family_of(a) or "other" for a, _, _ in preds})
        scored.append(
            {
                "rule_id": f"R{leaf['leaf']}",
                "leaf": leaf["leaf"],
                "predicates": [{"feature": a, "op": b, "threshold": c, "family": family_of(a)} for a, b, c in preds],
                "predicates_t": preds,
                "predicate_text": pretty(preds),
                "n_train": leaf["n_train"],
                "p40_train": p,
                "d1_base": base,
                "lift_train": p - base,
                "families": fams,
            }
        )
    scored.sort(key=lambda r: (-float(r["lift_train"]), -int(r["n_train"])))
    return scored[:MAX_FAVORABLE_RULES]


def fit_d1(events: list[dict[str, Any]]) -> dict[str, Any]:
    d1 = [r for r in events if str(r.get("block") or "") == TRAIN_BLOCK]
    n = len(d1)
    min_leaf = min_leaf_n(n)
    if n < min_leaf * 2:
        return {"ok": False, "reason": "d1_n", "d1_n": n, "min_leaf": min_leaf, "rules": [], "unstable_in_discovery": True}
    med = d1_medians(d1)
    X, y = matrix(d1, FEATURE_NAMES, med)
    fit = fit_tree(X, y, FEATURE_NAMES, min_leaf=min_leaf)
    if not fit.get("ok"):
        return {"ok": False, "reason": fit.get("reason"), "d1_n": n, "min_leaf": min_leaf, "rules": [], "unstable_in_discovery": True}
    stab = d1_family_stability(d1, med, min_leaf)
    rules = [] if stab.get("unstable") else extract_rules(fit["model"], d1, med, min_leaf)
    from research.native_causal_context_stack_discovery_v1.freeze import event_generator_sha256

    lock = {
        "TREE_SHA256": tree_sha256(
            rules_text=str(fit.get("rules_text") or ""),
            med=med,
            min_leaf=min_leaf,
            predicates=[r.get("predicates") for r in rules],
        ),
        "EVENT_GENERATOR_SHA256": event_generator_sha256(),
        "feature_names": list(FEATURE_NAMES),
        "dir_formulas": {
            "aligned_r5": "DIR * raw_r5",
            "aligned_r15": "DIR * raw_r15",
            "aligned_prior5_ret": "DIR * raw_prior5_ret",
            "aligned_vwap_bps": "DIR * (close - vwap) / vwap * 10000",
            "aligned_market_rel": "DIR * raw_mkt_rel",
            "aligned_sector_rel": "DIR * raw_sec_rel",
        },
        "d1_event_n": n,
        "d1_base_rate": float(np.mean(y)) if y.size else None,
        "min_samples_leaf": min_leaf,
        "max_depth": TREE_MAX_DEPTH,
        "criterion": TREE_CRITERION,
        "class_weight": None,
        "random_state": TREE_RANDOM_STATE,
        "selected_rule_ids": [r["rule_id"] for r in rules],
        "medians": med,
        "locked": True,
        "raw_unsigned_directional_excluded": True,
        "no_r14_threshold_reuse": True,
    }
    return {
        "ok": True,
        "d1_n": n,
        "min_leaf": min_leaf,
        "med": med,
        "fit": {k: v for k, v in fit.items() if k != "model"},
        "model": fit.get("model"),
        "stability": stab,
        "rules": [{k: v for k, v in r.items() if k != "predicates_t"} for r in rules],
        "rules_t": [r.get("predicates_t") for r in rules],
        "lock": lock,
        "unstable_in_discovery": bool(stab.get("unstable")),
    }

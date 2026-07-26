"""Univariate 4-class feature comparison."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.labels import CLASS_ORDER, MulticlassRow


def _safe_auc(y_bin: np.ndarray, scores: np.ndarray) -> Optional[float]:
    try:
        from sklearn.metrics import roc_auc_score

        if len(np.unique(y_bin)) < 2:
            return None
        return round(float(roc_auc_score(y_bin, scores)), 6)
    except Exception:
        return None


def class_feature_stats(
    feature: str,
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
) -> list[dict[str, Any]]:
    out = []
    for cls in CLASS_ORDER:
        idx = [i for i, r in enumerate(labeled) if r.class_label == cls]
        vals = []
        miss = 0
        for i in idx:
            v = rows[i].get(feature)
            if v is None:
                miss += 1
            else:
                vals.append(float(v))
        n = len(idx)
        a = np.array(vals, dtype=float) if vals else np.array([], dtype=float)
        out.append(
            {
                "feature": feature,
                "class": cls,
                "count": n,
                "n_observed": len(vals),
                "missing_rate": round(miss / n, 4) if n else None,
                "mean": round(float(np.mean(a)), 6) if len(a) else None,
                "median": round(float(np.median(a)), 6) if len(a) else None,
                "std": round(float(np.std(a)), 6) if len(a) > 1 else None,
                "p25": round(float(np.quantile(a, 0.25)), 6) if len(a) else None,
                "p75": round(float(np.quantile(a, 0.75)), 6) if len(a) else None,
            }
        )
    return out


def univariate_tests(
    feature: str,
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
) -> dict[str, Any]:
    y = np.array([r.class_label for r in labeled])
    x = np.array([np.nan if rows[i].get(feature) is None else float(rows[i][feature]) for i in range(len(rows))])
    mask = ~np.isnan(x)
    x_m, y_m = x[mask], y[mask]
    result: dict[str, Any] = {"feature": feature, "n_observed": int(mask.sum())}
    if mask.sum() < 40:
        result["note"] = "insufficient_observed"
        return result

    # Kruskal-Wallis
    try:
        from scipy.stats import kruskal

        groups = [x_m[y_m == c] for c in CLASS_ORDER if np.any(y_m == c)]
        groups = [g for g in groups if len(g) >= 5]
        if len(groups) >= 2:
            st = kruskal(*groups)
            result["kruskal_h"] = round(float(st.statistic), 6)
            result["kruskal_p"] = float(st.pvalue)
    except Exception as e:
        result["kruskal_error"] = str(e)[:80]

    # ANOVA
    try:
        from scipy.stats import f_oneway

        groups = [x_m[y_m == c] for c in CLASS_ORDER if np.sum(y_m == c) >= 5]
        if len(groups) >= 2:
            st = f_oneway(*groups)
            result["anova_f"] = round(float(st.statistic), 6)
            result["anova_p"] = float(st.pvalue)
    except Exception:
        pass

    # MI
    try:
        from sklearn.feature_selection import mutual_info_classif
        from research.winner_multiclass.labels import CLASS_TO_ID

        y_id = np.array([CLASS_TO_ID[c] for c in y_m])
        mi = mutual_info_classif(x_m.reshape(-1, 1), y_id, discrete_features=False, random_state=42)
        result["mutual_info"] = round(float(mi[0]), 8)
    except Exception:
        pass

    # one-vs-rest AUC + pairwise
    pairwise = {}
    for cls in CLASS_ORDER:
        y_bin = (y_m == cls).astype(int)
        # score = feature value (direction-agnostic via max(auc, 1-auc) later)
        auc = _safe_auc(y_bin, x_m)
        if auc is not None:
            pairwise[f"ovr_auc_{cls}"] = max(auc, 1.0 - auc)
    # key pairs
    pairs = [
        ("Winner", "STOP"),
        ("Winner", "NoProgress"),
        ("Winner", "Normal"),
        ("STOP", "NoProgress"),
        ("STOP", "Normal"),
        ("NoProgress", "Normal"),
    ]
    for a, b in pairs:
        m2 = (y_m == a) | (y_m == b)
        if m2.sum() < 20:
            continue
        y2 = (y_m[m2] == a).astype(int)
        auc = _safe_auc(y2, x_m[m2])
        if auc is not None:
            pairwise[f"auc_{a}_vs_{b}"] = max(auc, 1.0 - auc)
    result["effect"] = pairwise
    return result


def run_univariate(
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
    *,
    max_features: int = 60,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # prioritize by fill
    fills = []
    for f in feature_names:
        n = sum(1 for r in rows if r.get(f) is not None)
        fills.append((n, f))
    fills.sort(reverse=True)
    use = [f for _, f in fills[:max_features]]
    stats_rows = []
    test_rows = []
    for f in use:
        stats_rows.extend(class_feature_stats(f, rows, labeled))
        test_rows.append(univariate_tests(f, rows, labeled))
    return stats_rows, test_rows

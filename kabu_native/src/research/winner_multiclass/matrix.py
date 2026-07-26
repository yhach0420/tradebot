"""Matrix builders with lane-aware imputation policy."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.lanes import lane_of


def build_xy(
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    y: np.ndarray,
    *,
    train_idx: Optional[Sequence[int]] = None,
    impute_lanes: Sequence[str] = ("A",),
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    """Build X with train-only median impute for allowed lanes; Lane B/C stay NaN if missing.

    Rows with remaining NaN in used features are dropped from returned X/y (observed-complete).
    """
    names = list(feature_names)
    n = len(rows)
    X = np.full((n, len(names)), np.nan, dtype=float)
    for i, r in enumerate(rows):
        for j, k in enumerate(names):
            v = r.get(k)
            if v is not None:
                X[i, j] = float(v)

    idx_train = list(train_idx) if train_idx is not None else list(range(n))
    impute_set = set(impute_lanes)
    medians: dict[str, float] = {}
    for j, k in enumerate(names):
        if lane_of(k) not in impute_set:
            continue
        col = X[idx_train, j]
        m = col[~np.isnan(col)]
        if len(m):
            med = float(np.median(m))
            medians[k] = med
            miss = np.isnan(X[:, j])
            X[miss, j] = med

    # Keep rows that are complete after policy
    complete = ~np.isnan(X).any(axis=1)
    meta = {
        "n_in": n,
        "n_complete": int(complete.sum()),
        "medians_train": medians,
        "impute_lanes": list(impute_lanes),
        "dropped_incomplete": int((~complete).sum()),
    }
    return X[complete], y[complete], names, meta


def row_feature_dict(trade_features: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    """Passthrough; caller supplies enriched features from winner_feature_filter.build_feature_dict."""
    return dict(trade_features)

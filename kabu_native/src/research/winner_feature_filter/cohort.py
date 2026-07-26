"""Cohort-common feature extraction (Winner / STOP / NoProgress)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_feature_filter.labels import LabeledTrade


def _cohort_stats(
    values: Sequence[Optional[float]],
    mask: np.ndarray,
) -> dict[str, Any]:
    arr = np.array([np.nan if v is None else float(v) for v in values], dtype=float)
    sub = arr[mask]
    sub = sub[~np.isnan(sub)]
    if len(sub) == 0:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    return {
        "n": int(len(sub)),
        "mean": round(float(np.mean(sub)), 6),
        "median": round(float(np.median(sub)), 6),
        "p25": round(float(np.percentile(sub, 25)), 6),
        "p75": round(float(np.percentile(sub, 75)), 6),
    }


def extract_cohort_signatures(
    labeled: Sequence[LabeledTrade],
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    *,
    top_k: int = 25,
) -> dict[str, Any]:
    """Features that distinguish each cohort vs the rest (effect size + direction)."""
    cohorts = ("Winner", "STOP", "NoProgress")
    y_map = {
        "Winner": np.array([1 if r.cohort == "Winner" else 0 for r in labeled]),
        "STOP": np.array([1 if r.cohort == "STOP" else 0 for r in labeled]),
        "NoProgress": np.array([1 if r.cohort == "NoProgress" else 0 for r in labeled]),
    }
    out: dict[str, Any] = {}
    for cohort in cohorts:
        mask = y_map[cohort].astype(bool)
        rest = ~mask
        scored = []
        for name in feature_names:
            vals = [r.get(name) for r in rows]
            arr = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
            a = arr[mask]
            b = arr[rest]
            a = a[~np.isnan(a)]
            b = b[~np.isnan(b)]
            if len(a) < 15 or len(b) < 15:
                continue
            ma, mb = float(np.mean(a)), float(np.mean(b))
            sa, sb = float(np.std(a) + 1e-9), float(np.std(b) + 1e-9)
            # Cohen's d
            pooled = np.sqrt((sa**2 + sb**2) / 2.0)
            d = (ma - mb) / (pooled + 1e-12)
            # overlap of IQR direction: "common" if cohort median is extreme vs rest
            med_a, med_b = float(np.median(a)), float(np.median(b))
            scored.append(
                {
                    "feature": name,
                    "cohort_mean": round(ma, 6),
                    "rest_mean": round(mb, 6),
                    "cohort_median": round(med_a, 6),
                    "rest_median": round(med_b, 6),
                    "cohens_d": round(float(d), 6),
                    "abs_d": round(abs(float(d)), 6),
                    "direction": "higher" if ma > mb else "lower",
                    "cohort_stats": _cohort_stats(vals, mask),
                    "rest_stats": _cohort_stats(vals, rest),
                }
            )
        scored.sort(key=lambda r: -r["abs_d"])
        # "common" signature: top features with |d| >= 0.15
        common = [r for r in scored if r["abs_d"] >= 0.15][:top_k]
        out[cohort] = {
            "n": int(mask.sum()),
            "top_discriminators": scored[:top_k],
            "common_signature": common,
        }
    return out

"""Semantic ENTRY candidates: train-only quantile thresholds."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x21_entry_factory_exit_benchmark import FAMILY_BY_FEATURE, FEATURE_REGISTRY

from . import QUANTILE_NAMES

# Absolute-rise oriented directions (semantic ranks)
DIRECTIONS_SINGLE = (
    ("GE", 0.70),
    ("GE", 0.50),
    ("LE", 0.30),
)


def available_features(rows: list[dict[str, Any]], min_n: int = 200) -> list[str]:
    feats: list[str] = []
    for _fam, fs in FEATURE_REGISTRY.items():
        for f in fs:
            n = sum(1 for r in rows if r.get(f) is not None)
            if n >= min_n:
                feats.append(f)
    return feats


def feature_matrix(
    rows: list[dict[str, Any]], features: list[str]
) -> np.ndarray:
    n = len(rows)
    m = np.full((n, len(features)), np.nan, dtype=float)
    for j, f in enumerate(features):
        col = []
        for r in rows:
            v = r.get(f)
            if v is None:
                col.append(np.nan)
            else:
                try:
                    col.append(float(v))
                except (TypeError, ValueError):
                    col.append(np.nan)
        m[:, j] = np.asarray(col, dtype=float)
    return m


def build_semantic_catalog(features: list[str]) -> list[dict[str, Any]]:
    """
    single-feature + two-feature AND (cross-family) + directional interaction.
    Caps complexity: no 3+ conditions; limited AND space for nested-CV feasibility.
    """
    singles: list[dict[str, Any]] = []
    for f in features:
        fam = FAMILY_BY_FEATURE.get(f, "OTHER")
        for op, q in DIRECTIONS_SINGLE:
            qn = QUANTILE_NAMES[q]
            sid = f"{f}__{op.lower()}_{qn}"
            singles.append({
                "semantic_id": sid,
                "kind": "single",
                "feature": f,
                "family": fam,
                "op": op,
                "quantile": q,
                "quantile_name": qn,
                "n_features": 1,
            })

    # AND / interaction parents: GE q50/q70 only
    parents = [s for s in singles if s["op"] == "GE" and s["quantile"] in (0.50, 0.70)]
    by_fam: dict[str, list] = {}
    for p in parents:
        by_fam.setdefault(p["family"], []).append(p)

    # Prefer interpretable absolute-rise structure:
    # (symbol TREND/ACTIVITY/PRICE/REL) AND (MARKET_STATE q70)
    left_fams = ("TREND", "ACTIVITY", "PRICE_POSITION", "RELATIVE_STRENGTH", "RANGE_VOLATILITY")
    market_right = [b for b in by_fam.get("MARKET_STATE", []) if b["quantile"] == 0.70]
    ands: list[dict[str, Any]] = []
    for lf in left_fams:
        for a in by_fam.get(lf, []):
            if a["quantile"] != 0.70:
                continue
            for b in market_right:
                sid = f"AND__{a['semantic_id']}__{b['semantic_id']}"
                ands.append({
                    "semantic_id": sid,
                    "kind": "and",
                    "parents": [a["semantic_id"], b["semantic_id"]],
                    "features": [a["feature"], b["feature"]],
                    "families": [a["family"], b["family"]],
                    "n_features": 2,
                    "a": a,
                    "b": b,
                })

    # directional interaction: REL/TREND q70 × MARKET q50/q70
    interacts: list[dict[str, Any]] = []
    for a in list(by_fam.get("RELATIVE_STRENGTH", [])) + [
        x for x in by_fam.get("TREND", []) if x["quantile"] == 0.70
    ]:
        for b in by_fam.get("MARKET_STATE", []):
            sid = f"INTERACT__{a['semantic_id']}__{b['semantic_id']}"
            interacts.append({
                "semantic_id": sid,
                "kind": "interaction",
                "parents": [a["semantic_id"], b["semantic_id"]],
                "features": [a["feature"], b["feature"]],
                "families": [a["family"], b["family"]],
                "n_features": 2,
                "a": a,
                "b": b,
            })

    # Limited ACTIVITY × TREND ANDs (q70 only)
    for a in by_fam.get("TREND", []):
        if a["quantile"] != 0.70:
            continue
        for b in by_fam.get("ACTIVITY", []):
            if b["quantile"] != 0.70:
                continue
            sid = f"AND__{a['semantic_id']}__{b['semantic_id']}"
            ands.append({
                "semantic_id": sid,
                "kind": "and",
                "parents": [a["semantic_id"], b["semantic_id"]],
                "features": [a["feature"], b["feature"]],
                "families": [a["family"], b["family"]],
                "n_features": 2,
                "a": a,
                "b": b,
            })

    catalog = singles + ands + interacts
    # hard uniqueness
    seen = set()
    uniq = []
    for c in catalog:
        if c["semantic_id"] in seen:
            continue
        seen.add(c["semantic_id"])
        uniq.append(c)
    return uniq


def fit_thresholds(
    feat_mat: np.ndarray,
    features: list[str],
    train_idx: np.ndarray,
) -> dict[str, dict[float, float]]:
    """Train-only quantiles per feature. Never use test rows."""
    out: dict[str, dict[float, float]] = {}
    for j, f in enumerate(features):
        xs = feat_mat[train_idx, j]
        xs = xs[np.isfinite(xs)]
        if xs.size < 20:
            continue
        out[f] = {
            0.30: float(np.quantile(xs, 0.30)),
            0.50: float(np.quantile(xs, 0.50)),
            0.70: float(np.quantile(xs, 0.70)),
        }
    return out


def _single_mask(
    feat_mat: np.ndarray,
    features: list[str],
    thr: dict[str, dict[float, float]],
    spec: dict[str, Any],
) -> np.ndarray:
    f = spec["feature"]
    if f not in thr:
        return np.zeros(feat_mat.shape[0], dtype=bool)
    j = features.index(f)
    t = thr[f][spec["quantile"]]
    col = feat_mat[:, j]
    if spec["op"] == "GE":
        return np.isfinite(col) & (col >= t)
    return np.isfinite(col) & (col <= t)


def candidate_mask(
    feat_mat: np.ndarray,
    features: list[str],
    thr: dict[str, dict[float, float]],
    spec: dict[str, Any],
    single_cache: Optional[dict[str, np.ndarray]] = None,
) -> np.ndarray:
    if spec["kind"] == "single":
        m = _single_mask(feat_mat, features, thr, spec)
        if single_cache is not None:
            single_cache[spec["semantic_id"]] = m
        return m
    a = spec["a"]
    b = spec["b"]
    if single_cache is not None and a["semantic_id"] in single_cache:
        ma = single_cache[a["semantic_id"]]
    else:
        ma = _single_mask(feat_mat, features, thr, a)
        if single_cache is not None:
            single_cache[a["semantic_id"]] = ma
    if single_cache is not None and b["semantic_id"] in single_cache:
        mb = single_cache[b["semantic_id"]]
    else:
        mb = _single_mask(feat_mat, features, thr, b)
        if single_cache is not None:
            single_cache[b["semantic_id"]] = mb
    return ma & mb

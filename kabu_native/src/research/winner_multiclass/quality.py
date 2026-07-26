"""Data quality audit for Winner Multiclass features."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from research.winner_multiclass.labels import MulticlassRow
from research.winner_multiclass.lanes import SUSPECT_B_DAYS, is_time_or_id_feature, lane_of


def _arr(vals: Sequence[Optional[float]]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in vals], dtype=float)


def audit_feature(
    name: str,
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
) -> dict[str, Any]:
    vals = [r.get(name) for r in rows]
    a = _arr(vals)
    n = len(a)
    mask = ~np.isnan(a)
    n_ok = int(mask.sum())
    xs = a[mask]
    days_ok = sorted({labeled[i].trade.day for i in range(n) if mask[i]})
    flags = []
    if n_ok == 0:
        flags.append("all_missing")
    else:
        uniq = len(np.unique(np.round(xs, 6)))
        std = float(np.std(xs))
        if uniq <= 1:
            flags.append("constant")
        elif uniq <= 3 or std < 1e-6:
            flags.append("near_constant")
        # near 0.5 cluster for imbalance-like
        if "imb" in name.lower() and n_ok >= 20:
            frac = float(np.mean((xs >= 0.43) & (xs <= 0.53)))
            if frac >= 0.85 and std < 0.04:
                flags.append("suspect_narrow")
        # fallback 0.5
        if n_ok and float(np.mean(np.isclose(xs, 0.5, atol=1e-6))) > 0.3:
            flags.append("fallback_value_suspected")
    # day coverage for leakage heuristics: feature should not use exit fields
    leak_sub = ("pnl", "mfe", "mae", "exit_", "hold_sec", "is_winner")
    if any(s in name.lower() for s in leak_sub):
        flags.append("future_leakage_name")

    def _q(p: float) -> Optional[float]:
        if n_ok == 0:
            return None
        return round(float(np.quantile(xs, p)), 6)

    return {
        "feature": name,
        "lane": lane_of(name),
        "available_n": n_ok,
        "missing_n": n - n_ok,
        "available_rate": round(n_ok / n, 4) if n else 0.0,
        "first_date": days_ok[0] if days_ok else None,
        "last_date": days_ok[-1] if days_ok else None,
        "n_days": len(days_ok),
        "unique_n": int(len(np.unique(np.round(xs, 6)))) if n_ok else 0,
        "std": round(float(np.std(xs)), 6) if n_ok else None,
        "min": round(float(np.min(xs)), 6) if n_ok else None,
        "median": _q(0.5),
        "max": round(float(np.max(xs)), 6) if n_ok else None,
        "p01": _q(0.01),
        "p05": _q(0.05),
        "p25": _q(0.25),
        "p75": _q(0.75),
        "p95": _q(0.95),
        "p99": _q(0.99),
        "quality_flags": flags,
    }


def audit_all_features(
    feature_names: Sequence[str],
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
) -> list[dict[str, Any]]:
    out = []
    for name in feature_names:
        if is_time_or_id_feature(name):
            continue
        out.append(audit_feature(name, rows, labeled))
    out.sort(key=lambda r: (-(r["available_n"] or 0), r["feature"]))
    return out


def lane_b_daily_quality(
    rows: Sequence[Mapping[str, Optional[float]]],
    labeled: Sequence[MulticlassRow],
    feature: str = "f_imb",
) -> list[dict[str, Any]]:
    by: dict[str, list[float]] = defaultdict(list)
    for lt, r in zip(labeled, rows):
        v = r.get(feature)
        if v is not None:
            by[lt.trade.day].append(float(v))
    out = []
    for d in sorted(by):
        xs = np.array(by[d], dtype=float)
        std = float(np.std(xs)) if len(xs) > 1 else 0.0
        frac = float(np.mean((xs >= 0.43) & (xs <= 0.53)))
        flag = "SUSPECT_NARROW" if (d in SUSPECT_B_DAYS or (std < 0.04 and frac > 0.85)) else "OK"
        out.append(
            {
                "day": d,
                "feature": feature,
                "n": len(xs),
                "mean": round(float(np.mean(xs)), 6),
                "std": round(std, 6),
                "frac_0.43_0.53": round(frac, 4),
                "quality_flag": flag,
            }
        )
    return out


def bad_quality_features(audits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    bad = []
    for a in audits:
        flags = a.get("quality_flags") or []
        if flags and a.get("available_n", 0) > 0:
            if any(f in flags for f in ("constant", "near_constant", "suspect_narrow", "future_leakage_name", "fallback_value_suspected")):
                bad.append({"feature": a["feature"], "lane": a["lane"], "flags": flags, "available_n": a["available_n"]})
    return bad

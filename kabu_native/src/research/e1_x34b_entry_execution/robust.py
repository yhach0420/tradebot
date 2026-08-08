"""LODO/LOSO robustness on cross-fitted routed results; route profiles."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import DEC_AGG, DEC_PAS, DEC_SKIP, LODO_MIN_POSITIVE_DAYS
from .metrics import routed_net


def lodo_crossfit(rows: list[dict], decisions: list[str]) -> dict[str, Any]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for r, d in zip(rows, decisions):
        by_day[r["date"]].append(routed_net(r, d, 600))
    folds = []
    for hold in sorted(by_day):
        others = [x for day, xs in by_day.items() if day != hold for x in xs]
        if not others:
            continue
        folds.append({
            "holdout_day": hold,
            "holdout_mean": float(np.mean(by_day[hold])),
            "rest_mean": float(np.mean(others)),
        })
    pos = sum(1 for f in folds if f["holdout_mean"] > 0)
    return {
        "n_folds": len(folds),
        "positive_holdout_days": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "meets_min_positive_days": pos >= LODO_MIN_POSITIVE_DAYS,
        "mean_holdout": float(np.mean([f["holdout_mean"] for f in folds])) if folds else None,
        "folds": folds,
    }


def loso_crossfit(rows: list[dict], decisions: list[str], *, max_symbols: int = 40) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        nets = [
            routed_net(r, d, 600)
            for r, d in zip(rows, decisions)
            if r["symbol"] != hold
        ]
        if not nets:
            continue
        folds.append({
            "holdout_symbol": hold,
            "rest_mean": float(np.mean(nets)),
        })
    pos = sum(1 for f in folds if f["rest_mean"] > 0)
    return {
        "n_folds": len(folds),
        "positive_folds": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "mean_rest": float(np.mean([f["rest_mean"] for f in folds])) if folds else None,
        "min_rest": float(min(f["rest_mean"] for f in folds)) if folds else None,
        "sample": folds[:12],
    }


def route_profiles(rows: list[dict], decisions: list[str]) -> dict[str, Any]:
    """Pre-entry feature means by routed decision (explainability)."""
    feats = [
        "mid_ret_60s", "mid_ret_180s", "spread_bps", "imbalance",
        "event_rate_60s", "univ_med_mid_ret_60s", "tod_bucket",
    ]

    def _prof(dec: str) -> dict[str, Any]:
        sub = [r for r, d in zip(rows, decisions) if d == dec]
        out = {"n": len(sub)}
        for f in feats:
            xs = [float(r[f]) for r in sub if r.get(f) is not None and np.isfinite(r[f])]
            out[f] = float(np.mean(xs)) if xs else None
        return out

    return {
        "AGGRESSIVE": _prof(DEC_AGG),
        "PASSIVE": _prof(DEC_PAS),
        "SKIP": _prof(DEC_SKIP),
        "note": "means of pre-entry features only; fill outcome not included",
    }

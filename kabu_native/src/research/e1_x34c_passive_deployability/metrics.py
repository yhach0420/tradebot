"""Unlimited vs deployable metrics, LODO/LOSO."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import HORIZONS


def dist_stats(xs: list[float]) -> dict[str, Any]:
    a = np.asarray([x for x in xs if x is not None and np.isfinite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0, "mean": None, "median": None, "p90": None, "p95": None, "max": None, "min": None}
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "median": float(np.median(a)),
        "p90": float(np.quantile(a, 0.90)),
        "p95": float(np.quantile(a, 0.95)),
        "max": float(np.max(a)),
        "min": float(np.min(a)),
    }


def _pf(xs: list[float]) -> float | None:
    pos = sum(x for x in xs if x > 0)
    neg = sum(x for x in xs if x < 0)
    if abs(neg) < 1e-12:
        return None
    return float(pos / abs(neg))


def summarize_mode(
    events: list[dict],
    *,
    mode: str,
    ret_key_prefix: str = "fill_based_ret",
) -> dict[str, Any]:
    """
    mode:
      unlimited — all signals; unfilled/blocked contribute 0; fills use fill-based ret
      deployable — accepted fills only for filled mean; opp-weighted over all signals
                    (accepted fill ret, else 0)
    """
    n = len(events)
    fills = [e for e in events if e.get("filled")]
    accepted = [e for e in events if e.get("accepted")]
    blocked = [e for e in events if e.get("CAPACITY_BLOCKED") or e.get("DUPLICATE_BLOCKED")]

    def opp_series(H: int) -> list[float]:
        out = []
        for e in events:
            if mode == "unlimited":
                if e.get("filled") and e.get(f"{ret_key_prefix}_{H}") is not None:
                    out.append(float(e[f"{ret_key_prefix}_{H}"]))
                else:
                    out.append(0.0)
            else:  # deployable
                if e.get("accepted") and e.get(f"{ret_key_prefix}_{H}") is not None:
                    out.append(float(e[f"{ret_key_prefix}_{H}"]))
                else:
                    out.append(0.0)
        return out

    def filled_mean(H: int, subset: list[dict]) -> float | None:
        xs = [
            float(e[f"{ret_key_prefix}_{H}"])
            for e in subset
            if e.get(f"{ret_key_prefix}_{H}") is not None
        ]
        return float(np.mean(xs)) if xs else None

    def bal(series: list[float], group_fn) -> float | None:
        by: dict[Any, list[float]] = defaultdict(list)
        for e, v in zip(events, series):
            by[group_fn(e)].append(v)
        means = [float(np.mean(v)) for v in by.values() if v]
        return float(np.mean(means)) if means else None

    o600 = opp_series(600)
    by_day: dict[str, list[float]] = defaultdict(list)
    for e, v in zip(events, o600):
        by_day[e["date"]].append(v)
    day_means = {d: float(np.mean(v)) for d, v in by_day.items()}

    trade_subset = fills if mode == "unlimited" else accepted
    out: dict[str, Any] = {
        "mode": mode,
        "signals": n,
        "raw_fills": len(fills),
        "accepted_fills": len(accepted),
        "blocked_fills": len(blocked),
        "fills_per_day": float(len(trade_subset) / max(1, len(day_means))),
        "pf_equiv_600": _pf(o600),
        "positive_days": sum(1 for v in day_means.values() if v > 0),
        "negative_days": sum(1 for v in day_means.values() if v < 0),
        "n_days": len(day_means),
        "day_means": day_means,
        "ss_balanced_ret600": bal(
            o600, lambda e: (e["date"], e["symbol"], e["session"])
        ),
        "day_balanced_ret600": bal(o600, lambda e: e["date"]),
        "filled_mean_ret600": filled_mean(600, trade_subset),
    }
    for H in HORIZONS:
        out[f"opp_w_ret{H}"] = float(np.mean(opp_series(H)))
        out[f"filled_mean_ret{H}"] = filled_mean(H, trade_subset)
    return out


def signal_vs_fill_delta(fills: list[dict]) -> dict[str, Any]:
    out = {}
    for H in HORIZONS:
        deltas = [
            float(f[f"delta_fill_minus_signal_{H}"])
            for f in fills
            if f.get(f"delta_fill_minus_signal_{H}") is not None
        ]
        sig = [
            float(f[f"signal_based_ret_{H}"])
            for f in fills
            if f.get(f"signal_based_ret_{H}") is not None
        ]
        fil = [
            float(f[f"fill_based_ret_{H}"])
            for f in fills
            if f.get(f"fill_based_ret_{H}") is not None
        ]
        out[str(H)] = {
            "signal_based_mean": float(np.mean(sig)) if sig else None,
            "fill_based_mean": float(np.mean(fil)) if fil else None,
            "delta_mean": float(np.mean(deltas)) if deltas else None,
            "delta_stats": dist_stats(deltas),
        }
    return out


def lodo(events: list[dict], *, mode: str) -> dict[str, Any]:
    days = sorted({e["date"] for e in events})
    folds = []
    for hold in days:
        sub = [e for e in events if e["date"] != hold]
        sm = summarize_mode(sub, mode=mode)
        hold_sm = summarize_mode([e for e in events if e["date"] == hold], mode=mode)
        folds.append({
            "holdout_day": hold,
            "rest_opp600": sm.get("opp_w_ret600"),
            "holdout_opp600": hold_sm.get("opp_w_ret600"),
        })
    pos = sum(1 for f in folds if (f.get("holdout_opp600") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_holdout_days": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "mean_holdout": float(np.mean([f["holdout_opp600"] for f in folds if f["holdout_opp600"] is not None])) if folds else None,
        "folds": folds,
    }


def loso(events: list[dict], *, mode: str, max_symbols: int = 40) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        sub = [e for e in events if e["symbol"] != hold]
        sm = summarize_mode(sub, mode=mode)
        folds.append({"holdout_symbol": hold, "rest_opp600": sm.get("opp_w_ret600")})
    pos = sum(1 for f in folds if (f.get("rest_opp600") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_folds": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "mean_rest": float(np.mean([f["rest_opp600"] for f in folds if f["rest_opp600"] is not None])) if folds else None,
        "sample": folds[:12],
    }

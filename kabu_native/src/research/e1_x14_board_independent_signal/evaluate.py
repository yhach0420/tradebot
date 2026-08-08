"""Single-feature evaluation + component verdicts (no composite ENTRY)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import FEATURE_HYPOTHESIS

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"


def _spearman(x: list[float], y: list[float]) -> Optional[float]:
    if len(x) < 10:
        return None
    xr = np.argsort(np.argsort(np.asarray(x, dtype=float)))
    yr = np.argsort(np.argsort(np.asarray(y, dtype=float)))
    if np.std(xr) == 0 or np.std(yr) == 0:
        return None
    return float(np.corrcoef(xr, yr)[0, 1])


def _hyp_sign(name: str) -> float:
    h = FEATURE_HYPOTHESIS.get(name, "neutral")
    if h in ("positive", "pullback_positive"):
        return 1.0
    if h in ("negative", "late_chase_risk_negative"):
        return -1.0
    return 0.0


def evaluate_feature(name: str, clusters: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [c for c in clusters if c.get(name) is not None and c.get(LABEL) is not None]
    if len(rows) < 20:
        return {"feature": name, "support_clusters": len(rows), "status": "INSUFFICIENT",
                "hypothesis": FEATURE_HYPOTHESIS.get(name)}
    xs = [float(r[name]) for r in rows]
    ys = [float(r[LABEL]) for r in rows]
    # q20/q80 by feature
    q20, q80 = np.quantile(xs, 0.20), np.quantile(xs, 0.80)
    low = [ys[i] for i, x in enumerate(xs) if x <= q20]
    high = [ys[i] for i, x in enumerate(xs) if x >= q80]
    # apply hypothesis direction: effect = high - low for positive hyp
    sign = _hyp_sign(name)
    raw_gap = (float(np.mean(high)) - float(np.mean(low))) if low and high else None
    directed_gap = (raw_gap * sign) if (raw_gap is not None and sign != 0) else raw_gap

    # first-touch
    touch_low = [float(r[TOUCH]) for r in rows if r.get(TOUCH) is not None and float(r[name]) <= q20]
    touch_high = [float(r[TOUCH]) for r in rows if r.get(TOUCH) is not None and float(r[name]) >= q80]

    # day-balanced: mean of per-day directed gaps
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    day_gaps = []
    for d, rs in by_day.items():
        if len(rs) < 10:
            continue
        xv = [float(r[name]) for r in rs]
        yv = [float(r[LABEL]) for r in rs]
        a, b = np.quantile(xv, 0.20), np.quantile(xv, 0.80)
        lo = [yv[i] for i, x in enumerate(xv) if x <= a]
        hi = [yv[i] for i, x in enumerate(xv) if x >= b]
        if lo and hi:
            g = float(np.mean(hi)) - float(np.mean(lo))
            day_gaps.append(g * sign if sign else g)
    day_bal = float(np.mean(day_gaps)) if day_gaps else None

    by_sym: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    sym_gaps = []
    for s, rs in by_sym.items():
        if len(rs) < 8:
            continue
        xv = [float(r[name]) for r in rs]
        yv = [float(r[LABEL]) for r in rs]
        a, b = np.quantile(xv, 0.20), np.quantile(xv, 0.80)
        lo = [yv[i] for i, x in enumerate(xv) if x <= a]
        hi = [yv[i] for i, x in enumerate(xv) if x >= b]
        if lo and hi:
            g = float(np.mean(hi)) - float(np.mean(lo))
            sym_gaps.append(g * sign if sign else g)
    sym_bal = float(np.mean(sym_gaps)) if sym_gaps else None

    # LODO
    lodo = []
    days = sorted(by_day)
    for leave in days:
        sub = [r for r in rows if r["date"] != leave]
        if len(sub) < 30:
            continue
        xv = [float(r[name]) for r in sub]
        yv = [float(r[LABEL]) for r in sub]
        a, b = np.quantile(xv, 0.20), np.quantile(xv, 0.80)
        lo = [yv[i] for i, x in enumerate(xv) if x <= a]
        hi = [yv[i] for i, x in enumerate(xv) if x >= b]
        if lo and hi:
            g = (float(np.mean(hi)) - float(np.mean(lo)))
            lodo.append({"leave_day": leave, "directed_gap": g * sign if sign else g})
    lodo_flip = sum(1 for x in lodo if (day_bal or 0) > 0 and x["directed_gap"] < 0) if lodo else 0

    # LOSO rough
    loso_flip = 0
    for leave in list(by_sym)[: min(20, len(by_sym))]:
        sub = [r for r in rows if r["symbol"] != leave]
        if len(sub) < 30:
            continue
        xv = [float(r[name]) for r in sub]
        yv = [float(r[LABEL]) for r in sub]
        a, b = np.quantile(xv, 0.20), np.quantile(xv, 0.80)
        lo = [yv[i] for i, x in enumerate(xv) if x <= a]
        hi = [yv[i] for i, x in enumerate(xv) if x >= b]
        if lo and hi and day_bal and day_bal > 0:
            g = (float(np.mean(hi)) - float(np.mean(lo))) * (sign or 1)
            if g < 0:
                loso_flip += 1

    # contribution concentration
    day_contrib = []
    for d, rs in by_day.items():
        day_contrib.append((d, len(rs) / len(rows)))
    max_day = max(day_contrib, key=lambda x: x[1]) if day_contrib else ("", 0)
    sym_contrib = [(s, len(rs) / len(rows)) for s, rs in by_sym.items()]
    max_sym = max(sym_contrib, key=lambda x: x[1]) if sym_contrib else ("", 0)

    stable = (
        len(rows) >= 100
        and len(by_day) >= 5
        and day_bal is not None and day_bal > 0
        and lodo_flip <= max(1, len(lodo) // 3)
        and max_day[1] < 0.45
        and max_sym[1] < 0.35
    )
    return {
        "feature": name,
        "hypothesis": FEATURE_HYPOTHESIS.get(name),
        "hypothesis_sign_locked": True,
        "support_clusters": len(rows),
        "entry_days": len(by_day),
        "symbols_n": len(by_sym),
        "spearman": _spearman(xs, ys),
        "q20_forward_return": float(np.mean(low)) if low else None,
        "q80_forward_return": float(np.mean(high)) if high else None,
        "q80_minus_q20": raw_gap,
        "directed_q80_minus_q20": directed_gap,
        "q20_first_touch": float(np.mean(touch_low)) if touch_low else None,
        "q80_first_touch": float(np.mean(touch_high)) if touch_high else None,
        "day_balanced_effect": day_bal,
        "symbol_balanced_effect": sym_bal,
        "lodo": lodo,
        "lodo_flip_n": lodo_flip,
        "loso_flip_n": loso_flip,
        "max_single_day_contribution": {"day": max_day[0], "frac": max_day[1]},
        "max_single_symbol_contribution": {"symbol": max_sym[0], "frac": max_sym[1]},
        "stable_candidate": stable,
        "status": "STABLE" if stable else "UNSTABLE",
    }


PRICE_FEATURES = [
    "return_60s", "return_180s", "return_300s", "slope_60s", "slope_180s",
    "acceleration_30s_vs_prior30s", "distance_from_vwap_bps",
    "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
    "higher_low_180s", "lower_low_180s", "recent_high_break", "recent_low_break",
]
VOLUME_FEATURES = [
    "volume_rate_60s", "volume_ratio_30s_vs_prior120s", "volume_persistence_180s",
    "volume_active_fraction_180s", "trading_value_delta_60s",
]
RS_FEATURES = [
    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
    "symbol_minus_median_return_300s", "return_percentile_60s", "return_percentile_180s",
    "volume_percentile_60s", "trading_value_percentile_180s",
]


def component_verdict(results: list[dict[str, Any]], names: list[str], kind: str) -> dict[str, Any]:
    sub = [r for r in results if r["feature"] in names]
    stable = [r for r in sub if r.get("stable_candidate")]
    if kind == "price":
        v = "PRICE_PATH_SUPPORTED" if stable else "PRICE_PATH_NOT_SUPPORTED"
    elif kind == "volume":
        v = "VOLUME_CONTINUITY_SUPPORTED" if stable else "VOLUME_CONTINUITY_NOT_SUPPORTED"
    else:
        v = "RELATIVE_STRENGTH_SUPPORTED" if stable else "RELATIVE_STRENGTH_NOT_SUPPORTED"
    return {"verdict": v, "stable_features": [r["feature"] for r in stable],
            "unstable_features": [r["feature"] for r in sub if not r.get("stable_candidate")]}

"""Variant filters, risk distributions, incremental contrasts."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import (
    MIN_UNIVERSE,
    REBOUND_MIN_BPS,
    VOLUME_PERCENTILE_MIN,
    VWAP_UPPER_LIMIT_BPS,
)

LABEL = "forward_return_180s"
TOUCH = "plus5_before_minus5"


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _quantile(xs: list[float], q: float) -> Optional[float]:
    return float(np.quantile(xs, q)) if xs else None


def assign_variants(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag each C0 row with membership flags for A0–A4 / A2_Rejected."""
    out = []
    for r in rows:
        m = dict(r)
        m["in_A0"] = True
        vwap_ok = bool(m.get("vwap_evaluable")) and m.get("distance_from_vwap_bps") is not None
        m["in_A1"] = vwap_ok
        dist = m.get("distance_from_vwap_bps")
        m["in_A2"] = bool(vwap_ok and dist is not None and float(dist) <= VWAP_UPPER_LIMIT_BPS)
        m["in_A2_Rejected"] = bool(vwap_ok and dist is not None and float(dist) > VWAP_UPPER_LIMIT_BPS)
        reb = m.get("rebound_from_recent_low_bps")
        m["in_A3"] = bool(
            m["in_A2"] and reb is not None and float(reb) >= REBOUND_MIN_BPS
        )
        volp = m.get("volume_percentile_60s")
        uni = int(m.get("rs_universe_n") or 0)
        m["in_A4"] = bool(
            m["in_A3"]
            and volp is not None
            and float(volp) >= VOLUME_PERCENTILE_MIN
            and uni >= MIN_UNIVERSE
        )
        # sanity: A2 and A2_Rejected partition A1
        out.append(m)
    return out


def select(rows: list[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    key = f"in_{variant}"
    return [r for r in rows if r.get(key)]


def risk_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mae = [float(r["MAE_180s"]) for r in rows if r.get("MAE_180s") is not None]
    mfe = [float(r["MFE_180s"]) for r in rows if r.get("MFE_180s") is not None]
    # adverse MAE is more negative → p75/p90/p95 of adverse = lower quantiles of MAE
    ratios = []
    mfe_gt = []
    for r in rows:
        if r.get("MFE_180s") is None or r.get("MAE_180s") is None:
            continue
        mf, ma = float(r["MFE_180s"]), float(r["MAE_180s"])
        denom = abs(ma)
        if denom < 1e-15:
            continue
        ratios.append(mf / denom)
        mfe_gt.append(1.0 if mf > denom else 0.0)
    touch5 = [float(r[TOUCH]) for r in rows if r.get(TOUCH) is not None]
    touch10 = [float(r["plus10_before_minus10"]) for r in rows if r.get("plus10_before_minus10") is not None]
    return {
        "MAE_180": {
            "mean": _mean(mae),
            "median": float(np.median(mae)) if mae else None,
            "p75_adverse": _quantile(mae, 0.25),  # more negative side
            "p90_adverse": _quantile(mae, 0.10),
            "p95_adverse": _quantile(mae, 0.05),
            "worst": float(np.min(mae)) if mae else None,
        },
        "MFE_180": {
            "mean": _mean(mfe),
            "median": float(np.median(mfe)) if mfe else None,
            "p75": _quantile(mfe, 0.75),
            "p90": _quantile(mfe, 0.90),
            "p95": _quantile(mfe, 0.95),
            "best": float(np.max(mfe)) if mfe else None,
        },
        "payoff_excursion_ratio": {
            "mean": _mean(ratios),
            "median": float(np.median(ratios)) if ratios else None,
        },
        "P_MFE_gt_abs_MAE": _mean(mfe_gt),
        "P_plus5_before_minus5": _mean(touch5),
        "P_plus10_before_minus10": _mean(touch10),
    }


def variant_metrics(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    ok = select(rows, variant)
    fr = [float(r[LABEL]) for r in ok if r.get(LABEL) is not None]
    mfe180 = [float(r["MFE_180s"]) for r in ok if r.get("MFE_180s") is not None]
    mae180 = [float(r["MAE_180s"]) for r in ok if r.get("MAE_180s") is not None]
    touch = [float(r[TOUCH]) for r in ok if r.get(TOUCH) is not None]
    np_rate = _mean([1.0 if r.get("NO_PROGRESS_300S") else 0.0 for r in ok]) if ok else None

    by_day: dict[str, list[float]] = defaultdict(list)
    for r in ok:
        if r.get(LABEL) is not None:
            by_day[r["date"]].append(float(r[LABEL]))
    day_bal = _mean([float(np.mean(v)) for v in by_day.values() if v])

    days = sorted(by_day.keys())
    pos = sum(1 for v in by_day.values() if float(np.mean(v)) > 0)
    neg = sum(1 for v in by_day.values() if float(np.mean(v)) < 0)
    zero = sum(1 for v in by_day.values() if float(np.mean(v)) == 0)
    # days with no FR samples counted separately
    all_days = sorted({r["date"] for r in ok})
    insuff = len(all_days) - len(days)

    by_sym: dict[str, list[float]] = defaultdict(list)
    for r in ok:
        if r.get(LABEL) is not None:
            by_sym[r["symbol"]].append(float(r[LABEL]))
    sym_contrib = {s: len(v) / len(ok) for s, v in by_sym.items()} if ok else {}
    max_sym = max(sym_contrib.items(), key=lambda x: x[1]) if sym_contrib else ("", 0.0)
    top10 = sorted(sym_contrib.items(), key=lambda x: -x[1])[:10]

    day_rows = []
    for d in all_days:
        sub = [r for r in ok if r["date"] == d]
        day_rows.append({
            "date": d,
            "support": len(sub),
            "forward_return": _mean([float(r[LABEL]) for r in sub if r.get(LABEL) is not None]),
            "MFE_180": _mean([float(r["MFE_180s"]) for r in sub if r.get("MFE_180s") is not None]),
            "MAE_180": _mean([float(r["MAE_180s"]) for r in sub if r.get("MAE_180s") is not None]),
            "first_touch": _mean([float(r[TOUCH]) for r in sub if r.get(TOUCH) is not None]),
            "NoProgress": _mean([1.0 if r.get("NO_PROGRESS_300S") else 0.0 for r in sub]),
        })

    return {
        "variant": variant,
        "support": len(ok),
        "entry_days": len(all_days),
        "symbols_n": len({r["symbol"] for r in ok}),
        "mean_forward_return_180s": _mean(fr),
        "day_balanced_forward_return": day_bal,
        "mean_MFE_180s": _mean(mfe180),
        "mean_MAE_180s": _mean(mae180),
        "mean_first_touch_plus5_before_minus5": _mean(touch),
        "no_progress_rate": np_rate,
        "positive_days": pos,
        "negative_days": neg,
        "zero_or_insufficient_days": zero + insuff,
        "max_single_symbol_contribution": {"symbol": max_sym[0], "frac": max_sym[1]},
        "top10_symbol_contribution": [{"symbol": s, "frac": f} for s, f in top10],
        "risk": risk_distribution(ok),
        "daily": day_rows,
        "forward_return_30s": _mean([float(r["forward_return_30s"]) for r in ok if r.get("forward_return_30s") is not None]),
        "forward_return_60s": _mean([float(r["forward_return_60s"]) for r in ok if r.get("forward_return_60s") is not None]),
        "forward_return_300s": _mean([float(r["forward_return_300s"]) for r in ok if r.get("forward_return_300s") is not None]),
        "mean_MFE_60s": _mean([float(r["MFE_60s"]) for r in ok if r.get("MFE_60s") is not None]),
        "mean_MAE_60s": _mean([float(r["MAE_60s"]) for r in ok if r.get("MAE_60s") is not None]),
        "mean_MFE_300s": _mean([float(r["MFE_300s"]) for r in ok if r.get("MFE_300s") is not None]),
        "mean_MAE_300s": _mean([float(r["MAE_300s"]) for r in ok if r.get("MAE_300s") is not None]),
        "mean_plus5_before_minus10": _mean([float(r["plus5_before_minus10"]) for r in ok if r.get("plus5_before_minus10") is not None]),
        "mean_plus10_before_minus10": _mean([float(r["plus10_before_minus10"]) for r in ok if r.get("plus10_before_minus10") is not None]),
        "mean_plus10_before_minus15": _mean([float(r["plus10_before_minus15"]) for r in ok if r.get("plus10_before_minus15") is not None]),
        "mean_time_to_plus5": _mean([float(r["time_to_plus5"]) for r in ok if r.get("time_to_plus5") is not None]),
        "mean_time_to_plus10": _mean([float(r["time_to_plus10"]) for r in ok if r.get("time_to_plus10") is not None]),
    }


def incremental(a: dict[str, Any], b: dict[str, Any], label: str) -> dict[str, Any]:
    def d(key: str) -> Optional[float]:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            return None
        return float(va - vb)

    return {
        "contrast": label,
        "forward_return_delta": d("mean_forward_return_180s"),
        "day_balanced_fr_delta": d("day_balanced_forward_return"),
        "MFE_180_delta": d("mean_MFE_180s"),
        "MAE_180_delta": d("mean_MAE_180s"),
        "first_touch_delta": d("mean_first_touch_plus5_before_minus5"),
        "NoProgress_delta": d("no_progress_rate"),
        "support_delta": (a.get("support") or 0) - (b.get("support") or 0),
    }


def age_dist(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": float(np.mean(vals)),
        "median": float(np.median(vals)),
        "p90": float(np.quantile(vals, 0.9)),
        "max": float(np.max(vals)),
    }


def availability_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    a0 = select(rows, "A0")
    eval_rows = [r for r in a0 if r.get("vwap_evaluable")]
    not_rows = [r for r in a0 if not r.get("vwap_evaluable")]
    frac = len(eval_rows) / len(a0) if a0 else 0.0

    def outcome_block(sub: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(sub),
            "forward_return_180s": _mean([float(r[LABEL]) for r in sub if r.get(LABEL) is not None]),
            "MFE_180": _mean([float(r["MFE_180s"]) for r in sub if r.get("MFE_180s") is not None]),
            "MAE_180": _mean([float(r["MAE_180s"]) for r in sub if r.get("MAE_180s") is not None]),
            "NoProgress": _mean([1.0 if r.get("NO_PROGRESS_300S") else 0.0 for r in sub]) if sub else None,
        }

    return {
        "feature_evaluable_fraction": frac,
        "price_age_distribution": age_dist([float(r["price_age_sec"]) for r in a0 if r.get("price_age_sec") is not None]),
        "volume_age_distribution": age_dist([float(r["volume_age_sec"]) for r in a0 if r.get("volume_age_sec") is not None]),
        "vwap_age_distribution": age_dist([float(r["vwap_age_sec"]) for r in a0 if r.get("vwap_age_sec") is not None]),
        "evaluable": outcome_block(eval_rows),
        "not_evaluable": outcome_block(not_rows),
    }


def reject_gate(a2: dict[str, Any], rej: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    ok = True
    if (rej.get("support") or 0) < 30:
        reasons.append("reject_support_lt_30")
        ok = False
    if (rej.get("entry_days") or 0) < 5:
        reasons.append("reject_days_lt_5")
        ok = False
    if (rej.get("day_balanced_forward_return") or 0) >= (a2.get("day_balanced_forward_return") or 0):
        reasons.append("rejected_day_bal_not_worse")
        ok = False
    if (rej.get("mean_first_touch_plus5_before_minus5") or 0) >= (a2.get("mean_first_touch_plus5_before_minus5") or 0):
        reasons.append("rejected_touch_not_worse")
        ok = False
    # MAE worse = more negative for rejected
    if rej.get("mean_MAE_180s") is not None and a2.get("mean_MAE_180s") is not None:
        if rej["mean_MAE_180s"] >= a2["mean_MAE_180s"]:
            reasons.append("rejected_MAE_not_worse")
            ok = False
    else:
        reasons.append("missing_MAE")
        ok = False
    return {"pass": ok, "reasons": reasons}


def exclude_symbols(rows: list[dict[str, Any]], symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    ban = set(symbols)
    return [r for r in rows if r["symbol"] not in ban]

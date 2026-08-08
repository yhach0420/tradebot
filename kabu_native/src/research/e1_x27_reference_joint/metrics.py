"""Route-level aggregation, selection/adaptation deltas, joint classification."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from . import (
    CONSUMED_DAY,
    DISCOVERY,
    EVALUATION,
    MIN_COVERAGE,
    MIN_DAYS,
    MIN_SYMBOLS,
    MIN_TRADES,
    PRIMARY_CONTROL,
    STRESS_DAY,
)


def period_mask(dates: np.ndarray, period: str) -> np.ndarray:
    if period == "DISCOVERY":
        return np.isin(dates, list(DISCOVERY))
    if period == "EVALUATION":
        return np.isin(dates, list(EVALUATION))
    if period == "20260803":
        return dates == STRESS_DAY
    if period == "20260804":
        return dates == CONSUMED_DAY
    if period == "ALL":
        return np.isin(dates, list(DISCOVERY + EVALUATION + (STRESS_DAY,)))
    raise ValueError(period)


def _pf(pnls: np.ndarray) -> tuple[Optional[float], str]:
    gains = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    g = float(np.sum(gains)) if gains.size else 0.0
    l = float(np.abs(np.sum(losses))) if losses.size else 0.0
    if l == 0 and g == 0:
        return None, "PF_UNDEFINED_ZERO"
    if l == 0 and g > 0:
        return None, "PF_UNDEFINED_NO_LOSS"
    if g == 0 and l > 0:
        return 0.0, "PF_ZERO_GAIN"
    return g / l, "OK"


def _seq_dd(pnls: np.ndarray, dates: np.ndarray, idx: np.ndarray) -> Optional[float]:
    if idx.size == 0:
        return None
    order = np.lexsort((idx, dates[idx]))
    ordered = pnls[idx][order]
    cum = np.cumsum(ordered)
    peak = np.maximum.accumulate(cum)
    return float(np.min(cum - peak))


def summarize_mask(
    *,
    mat: dict[str, np.ndarray],
    mask: np.ndarray,
    dates: np.ndarray,
    symbols: np.ndarray,
    sessions: np.ndarray,
    period: str,
    population: str,
) -> dict[str, Any]:
    pm = period_mask(dates, period)
    if population == "SELECTED":
        base = pm & mask
    elif population == "COMPLEMENT":
        base = pm & ~mask
    elif population == "ALL_ANCHORS":
        base = pm
    else:
        raise ValueError(population)
    # coverage = OK ledgers / same population×period eligible (not all anchors)
    elig_denom = int(base.sum())
    period_n = int(pm.sum())
    valid = base & mat["valid"]
    idx = np.where(valid)[0]
    n = int(idx.size)
    coverage = (n / elig_denom) if elig_denom else None
    if n == 0:
        return {
            "population": population, "period": period, "trades": 0,
            "eligible_trades": elig_denom, "eligible_period": period_n,
            "coverage": coverage, "days": 0, "symbols": 0, "sessions": 0,
            "avg_return_bps": None, "median_return_bps": None,
            "day_balanced_return_bps": None, "symbol_balanced_return_bps": None,
            "avg_pnl": None, "median_pnl": None, "total_pnl": None,
            "win_rate": None, "profit_factor": None, "pf_status": "NO_TRADES",
            "best_trade": None, "worst_trade": None, "episode_seq_max_dd": None,
            "positive_days": 0, "negative_days": 0,
            "median_hold_sec": None, "exit_reason_counts": {},
        }
    rets = mat["ret_bps"][idx]
    pnls = mat["pnl"][idx]
    holds = mat["hold"][idx]
    d = dates[idx]
    s = symbols[idx]
    uniq_d, inv_d = np.unique(d, return_inverse=True)
    day_ret = np.bincount(inv_d, weights=rets) / np.maximum(np.bincount(inv_d), 1)
    uniq_s, inv_s = np.unique(s, return_inverse=True)
    sym_ret = np.bincount(inv_s, weights=rets) / np.maximum(np.bincount(inv_s), 1)
    day_pnl = np.bincount(inv_d, weights=pnls)
    pf, pf_st = _pf(pnls)
    reasons = Counter(mat["reason"][idx].tolist())
    return {
        "population": population, "period": period,
        "trades": n, "eligible_trades": elig_denom, "eligible_period": period_n, "coverage": coverage,
        "days": int(uniq_d.size), "symbols": int(uniq_s.size),
        "sessions": int(np.unique(sessions[idx]).size),
        "avg_return_bps": float(np.mean(rets)),
        "median_return_bps": float(np.median(rets)),
        "day_balanced_return_bps": float(np.mean(day_ret)),
        "symbol_balanced_return_bps": float(np.mean(sym_ret)),
        "avg_pnl": float(np.mean(pnls)),
        "median_pnl": float(np.median(pnls)),
        "total_pnl": float(np.sum(pnls)),
        "win_rate": float(np.mean(pnls > 0)),
        "profit_factor": pf, "pf_status": pf_st,
        "best_trade": float(np.max(pnls)), "worst_trade": float(np.min(pnls)),
        "episode_seq_max_dd": _seq_dd(mat["pnl"], dates, idx),
        "positive_days": int(np.sum(day_pnl > 0)),
        "negative_days": int(np.sum(day_pnl < 0)),
        "median_hold_sec": float(np.median(holds)),
        "q25_hold_sec": float(np.quantile(holds, 0.25)),
        "q75_hold_sec": float(np.quantile(holds, 0.75)),
        "exit_reason_counts": dict(reasons),
        "max_day_contribution": float(np.max(np.abs(day_pnl))) if day_pnl.size else None,
        "max_symbol_contribution": float(np.max(np.abs(sym_ret))) if sym_ret.size else None,
    }


def delta_avg(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(a - b)


def pairwise_common(
    *,
    mat_a: dict[str, np.ndarray],
    mat_b: dict[str, np.ndarray],
    selected: np.ndarray,
    dates: np.ndarray,
    period: str,
) -> dict[str, Any]:
    pm = period_mask(dates, period) & selected
    common = pm & mat_a["valid"] & mat_b["valid"]
    idx = np.where(common)[0]
    if idx.size == 0:
        return {"n": 0, "delta_avg_return": None, "delta_avg_pnl": None,
                "family_avg_return": None, "control_avg_return": None}
    ra = mat_a["ret_bps"][idx]
    rb = mat_b["ret_bps"][idx]
    pa = mat_a["pnl"][idx]
    pb = mat_b["pnl"][idx]
    return {
        "n": int(idx.size),
        "family_avg_return": float(np.mean(ra)),
        "control_avg_return": float(np.mean(rb)),
        "delta_avg_return": float(np.mean(ra) - np.mean(rb)),
        "family_avg_pnl": float(np.mean(pa)),
        "control_avg_pnl": float(np.mean(pb)),
        "delta_avg_pnl": float(np.mean(pa) - np.mean(pb)),
        "delta_median_hold": float(np.median(mat_a["hold"][idx]) - np.median(mat_b["hold"][idx])),
        "delta_worst_trade": float(np.min(pa) - np.min(pb)),
        "delta_dd": None,  # filled by caller if needed
    }


def support_ok(sel: dict[str, Any]) -> bool:
    cov = sel.get("coverage")
    return (
        (sel.get("trades") or 0) >= MIN_TRADES
        and (sel.get("days") or 0) >= MIN_DAYS
        and (sel.get("symbols") or 0) >= MIN_SYMBOLS
        and cov is not None and cov >= MIN_COVERAGE
    )


def classify_family_route(
    *,
    sel: dict[str, Any],
    entry_delta: Optional[float],
    exit_delta: Optional[float],
) -> str:
    if not support_ok(sel):
        return "REFERENCE_SUPPORT_INSUFFICIENT"
    avg_pnl = sel.get("avg_pnl")
    pf = sel.get("profit_factor")
    pf_ok = pf is not None and pf > 1.0
    abs_pos = avg_pnl is not None and avg_pnl > 0 and pf_ok
    entry_pos = entry_delta is not None and entry_delta > 0
    exit_pos = exit_delta is not None and exit_delta > 0
    if abs_pos and entry_pos and exit_pos:
        return "REFERENCE_JOINT_EDGE_POSITIVE"
    if entry_pos and not exit_pos:
        return "REFERENCE_ENTRY_SELECTION_ONLY"
    if exit_pos and not entry_pos:
        return "REFERENCE_EXIT_ADAPTATION_ONLY"
    if abs_pos:
        return "REFERENCE_ABSOLUTE_POSITIVE_ONLY"
    # mixed / weak
    if (entry_delta is not None and exit_delta is not None
            and ((entry_delta > 0) != (exit_delta > 0))):
        return "REFERENCE_MIXED"
    if avg_pnl is not None and entry_delta is not None and (
        (avg_pnl > 0) != (entry_delta > 0)
    ):
        return "REFERENCE_MIXED"
    return "REFERENCE_MIXED"


def classify_common_control(
    *,
    sel: dict[str, Any],
    entry_delta: Optional[float],
) -> str:
    if not support_ok(sel):
        return "COMMON_CONTROL_ENTRY_INSUFFICIENT"
    if entry_delta is not None and entry_delta > 0 and (sel.get("avg_pnl") or 0) > 0:
        return "COMMON_CONTROL_ENTRY_POSITIVE"
    if entry_delta is not None and entry_delta > 0:
        return "COMMON_CONTROL_ENTRY_MIXED"
    return "COMMON_CONTROL_ENTRY_WEAK"

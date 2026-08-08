"""Executable metrics, selection/adaptation, classifications."""
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


def summarize(
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
    elig = int(base.sum())
    valid = base & mat["valid"]
    idx = np.where(valid)[0]
    n = int(idx.size)
    coverage = (n / elig) if elig else None
    if n == 0:
        return {
            "population": population, "period": period, "trades": 0,
            "eligible": elig, "coverage": coverage, "days": 0, "symbols": 0,
            "avg_return_bps": None, "avg_pnl": None, "median_pnl": None,
            "day_balanced_pnl": None, "symbol_balanced_pnl": None,
            "profit_factor": None, "pf_status": "NO_TRADES",
            "win_rate": None, "worst_trade": None, "episode_seq_max_dd": None,
            "positive_days": 0, "negative_days": 0, "median_hold_sec": None,
            "exit_reason_counts": {}, "total_pnl": None,
            "max_day_contribution": None, "max_symbol_contribution": None,
        }
    rets = mat["ret_bps"][idx]
    pnls = mat["pnl"][idx]
    holds = mat["hold"][idx]
    d = dates[idx]
    s = symbols[idx]
    uniq_d, inv_d = np.unique(d, return_inverse=True)
    day_pnl = np.bincount(inv_d, weights=pnls)
    day_ret = np.bincount(inv_d, weights=rets) / np.maximum(np.bincount(inv_d), 1)
    uniq_s, inv_s = np.unique(s, return_inverse=True)
    sym_pnl = np.bincount(inv_s, weights=pnls) / np.maximum(np.bincount(inv_s), 1)
    pf, pf_st = _pf(pnls)
    order = np.lexsort((idx, d))
    cum = np.cumsum(pnls[order])
    peak = np.maximum.accumulate(cum)
    dd = float(np.min(cum - peak))
    return {
        "population": population, "period": period,
        "trades": n, "eligible": elig, "coverage": coverage,
        "days": int(uniq_d.size), "symbols": int(uniq_s.size),
        "sessions": int(np.unique(sessions[idx]).size),
        "avg_return_bps": float(np.mean(rets)),
        "median_return_bps": float(np.median(rets)),
        "avg_pnl": float(np.mean(pnls)),
        "median_pnl": float(np.median(pnls)),
        "total_pnl": float(np.sum(pnls)),
        "day_balanced_pnl": float(np.mean(day_pnl)),
        "symbol_balanced_pnl": float(np.mean(sym_pnl)),
        "day_balanced_return_bps": float(np.mean(day_ret)),
        "profit_factor": pf, "pf_status": pf_st,
        "win_rate": float(np.mean(pnls > 0)),
        "worst_trade": float(np.min(pnls)),
        "best_trade": float(np.max(pnls)),
        "episode_seq_max_dd": dd,
        "positive_days": int(np.sum(day_pnl > 0)),
        "negative_days": int(np.sum(day_pnl < 0)),
        "median_hold_sec": float(np.median(holds)),
        "exit_reason_counts": dict(Counter(mat["reason"][idx].tolist())),
        "max_day_contribution": float(np.max(np.abs(day_pnl))),
        "max_symbol_contribution": float(np.max(np.abs(sym_pnl))),
    }


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
    ra, rb = mat_a["ret_bps"][idx], mat_b["ret_bps"][idx]
    pa, pb = mat_a["pnl"][idx], mat_b["pnl"][idx]
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
        "view": "PAIRWISE_COMMON_EXECUTABLE_EPISODE_VIEW",
    }


def support_ok(sel: dict[str, Any]) -> bool:
    cov = sel.get("coverage")
    return (
        (sel.get("trades") or 0) >= MIN_TRADES
        and (sel.get("days") or 0) >= MIN_DAYS
        and (sel.get("symbols") or 0) >= MIN_SYMBOLS
        and cov is not None and cov >= MIN_COVERAGE
    )


def reclassify_x27_joint(
    *,
    x27_status: str,
    avg_pnl: Optional[float],
    avg_ret: Optional[float],
    pf: Optional[float],
    entry_delta: Optional[float],
    exit_delta: Optional[float],
) -> str:
    if x27_status != "REFERENCE_JOINT_EDGE_POSITIVE":
        return "NOT_X27_JOINT"
    pnl_pos = avg_pnl is not None and avg_pnl > 0
    ret_pos = avg_ret is not None and avg_ret > 0
    pf_ok = pf is not None and pf > 1
    entry_pos = entry_delta is not None and entry_delta > 0
    exit_pos = exit_delta is not None and exit_delta > 0
    if pnl_pos and pf_ok and ret_pos and entry_pos and exit_pos:
        return "REFERENCE_DIRECTIONAL_JOINT_POSITIVE"
    if pnl_pos and avg_ret is not None and avg_ret <= 0:
        return "REFERENCE_YEN_POSITIVE_BPS_NONPOSITIVE"
    return "REFERENCE_JOINT_OTHER"


def classify_executable(
    *,
    sel_full: dict[str, Any],
    entry_delta: Optional[float],
    exit_delta: Optional[float],
    x27_reclass: str,
    bridge_directional: bool,
) -> str:
    if not support_ok(sel_full):
        return "EXECUTABLE_SUPPORT_INSUFFICIENT"
    avg_pnl = sel_full.get("avg_pnl")
    avg_ret = sel_full.get("avg_return_bps")
    pf = sel_full.get("profit_factor")
    pf_ok = pf is not None and pf > 1
    pnl_pos = avg_pnl is not None and avg_pnl > 0
    ret_pos = avg_ret is not None and avg_ret > 0
    entry_pos = entry_delta is not None and entry_delta > 0
    exit_pos = exit_delta is not None and exit_delta > 0

    directional = pnl_pos and pf_ok and ret_pos and entry_pos and exit_pos
    if directional:
        return "EXECUTABLE_DIRECTIONAL_JOINT_POSITIVE"
    if pnl_pos and pf_ok and avg_ret is not None and avg_ret <= 0:
        return "EXECUTABLE_YEN_POSITIVE_BPS_NONPOSITIVE"
    if x27_reclass == "REFERENCE_DIRECTIONAL_JOINT_POSITIVE" and not directional:
        return "EXECUTION_COST_SENSITIVE"
    if bridge_directional and not directional:
        return "EXECUTION_TRIGGER_SENSITIVE"
    if entry_pos and not exit_pos:
        return "EXECUTABLE_ENTRY_SELECTION_ONLY"
    if exit_pos and not entry_pos:
        return "EXECUTABLE_EXIT_ADAPTATION_ONLY"
    if pnl_pos and pf_ok:
        return "EXECUTABLE_ABSOLUTE_POSITIVE_ONLY"
    return "EXECUTABLE_MIXED"


def bridge_is_directional(sel_bridge: dict[str, Any], entry_d: Optional[float], exit_d: Optional[float]) -> bool:
    if not support_ok(sel_bridge):
        return False
    return (
        (sel_bridge.get("avg_pnl") or 0) > 0
        and (sel_bridge.get("profit_factor") is not None and sel_bridge["profit_factor"] > 1)
        and (sel_bridge.get("avg_return_bps") or 0) > 0
        and (entry_d or 0) > 0
        and (exit_d or 0) > 0
    )

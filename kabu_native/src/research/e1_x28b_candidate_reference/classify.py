"""X28B classification + personalization helpers (reuse X27 summarize)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x27_reference_joint.metrics import (
    delta_avg,
    pairwise_common,
    period_mask,
    summarize_mask,
    support_ok,
)

from . import MIN_COMMON_EPISODES, MIN_COVERAGE, MIN_DAYS, MIN_SYMBOLS, MIN_TRADES


def stop_risk_tag(stop_bps: Optional[float]) -> Optional[str]:
    if stop_bps is None or stop_bps == "":
        return None
    try:
        s = float(stop_bps)
    except (TypeError, ValueError):
        return None
    if s <= 50:
        return "NORMAL_STOP"
    if s <= 100:
        return "WIDE_STOP"
    return "VERY_WIDE_STOP"


def personalization_pairwise(
    *,
    mat_specific: dict[str, np.ndarray],
    mat_family: dict[str, np.ndarray],
    selected: np.ndarray,
    dates: np.ndarray,
    period: str = "EVALUATION",
) -> dict[str, Any]:
    """specific minus family on common selected episodes."""
    pm = period_mask(dates, period) & selected
    common = pm & mat_specific["valid"] & mat_family["valid"]
    idx = np.where(common)[0]
    n = int(idx.size)
    if n == 0:
        return {
            "n": 0, "delta_avg_return": None, "delta_avg_pnl": None,
            "specific_better_episode_rate": None, "family_better_episode_rate": None,
            "same_episode_rate": None,
            "day_balanced_delta": None, "symbol_balanced_delta": None,
            "delta_pf": None, "delta_worst": None, "delta_hold": None,
            "coverage_delta": None,
        }
    rs = mat_specific["ret_bps"][idx]
    rf = mat_family["ret_bps"][idx]
    ps = mat_specific["pnl"][idx]
    pf = mat_family["pnl"][idx]
    dret = rs - rf
    better_s = float(np.mean(dret > 1e-12))
    better_f = float(np.mean(dret < -1e-12))
    same = float(np.mean(np.abs(dret) <= 1e-12))
    # day / symbol balanced deltas
    d = dates[idx]
    uniq_d, inv_d = np.unique(d, return_inverse=True)
    day_ds = np.bincount(inv_d, weights=rs) / np.maximum(np.bincount(inv_d), 1)
    day_df = np.bincount(inv_d, weights=rf) / np.maximum(np.bincount(inv_d), 1)

    def _pf(pnls: np.ndarray) -> Optional[float]:
        g = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 0.0
        l = float(np.abs(np.sum(pnls[pnls < 0]))) if np.any(pnls < 0) else 0.0
        if l == 0:
            return None
        return g / l

    pfs, pff = _pf(ps), _pf(pf)
    return {
        "n": n,
        "delta_avg_return": float(np.mean(rs) - np.mean(rf)),
        "delta_avg_pnl": float(np.mean(ps) - np.mean(pf)),
        "specific_avg_return": float(np.mean(rs)),
        "family_avg_return": float(np.mean(rf)),
        "specific_better_episode_rate": better_s,
        "family_better_episode_rate": better_f,
        "same_episode_rate": same,
        "day_balanced_delta": float(np.mean(day_ds - day_df)),
        "symbol_balanced_delta": None,  # filled if symbols passed
        "delta_pf": (float(pfs - pff) if pfs is not None and pff is not None else None),
        "delta_worst": float(np.min(ps) - np.min(pf)),
        "delta_hold": float(np.median(mat_specific["hold"][idx]) - np.median(mat_family["hold"][idx])),
        "coverage_delta": None,
    }


def entry_selection_support_ok(n_common_or_selected: int) -> bool:
    return n_common_or_selected >= MIN_COMMON_EPISODES


def personalization_support_ok(n: int) -> bool:
    return n >= MIN_COMMON_EPISODES


def abs_directional_positive(sel: dict[str, Any]) -> bool:
    avg_pnl = sel.get("avg_pnl")
    pf = sel.get("profit_factor")
    avg_ret = sel.get("avg_return_bps")
    pf_ok = pf is not None and pf > 1.0
    return (
        avg_pnl is not None and avg_pnl > 0
        and pf_ok
        and avg_ret is not None and avg_ret > 0
    )


def yen_only_positive(sel: dict[str, Any]) -> bool:
    avg_pnl = sel.get("avg_pnl")
    pf = sel.get("profit_factor")
    avg_ret = sel.get("avg_return_bps")
    pf_ok = pf is not None and pf > 1.0
    return (
        avg_pnl is not None and avg_pnl > 0
        and pf_ok
        and (avg_ret is None or avg_ret <= 0)
    )


def classify_specific(
    *,
    is_fallback: bool,
    sel: dict[str, Any],
    entry_delta: Optional[float],
    pers_delta: Optional[float],
    entry_n: int,
    pers_n: int,
) -> str:
    if is_fallback:
        return "FALLBACK_NO_PERSONALIZATION_TEST"

    if not support_ok(sel):
        return "SPECIFIC_SUPPORT_INSUFFICIENT"

    # require comparison episode support for entry/pers when used in joint
    entry_ok = entry_selection_support_ok(entry_n) if entry_n else (sel.get("trades") or 0) >= MIN_TRADES
    pers_ok = personalization_support_ok(pers_n)

    abs_pos = abs_directional_positive(sel)
    entry_pos = entry_delta is not None and entry_delta > 0 and entry_ok
    pers_pos = pers_delta is not None and pers_delta > 0 and pers_ok
    entry_nonpos = entry_delta is not None and entry_delta <= 0
    pers_nonpos = pers_delta is not None and pers_delta <= 0

    if abs_pos and entry_pos and pers_pos:
        return "SPECIFIC_DIRECTIONAL_JOINT_POSITIVE"

    if abs_pos and entry_pos and (pers_nonpos or (pers_delta is not None and not pers_pos)):
        return "SPECIFIC_ENTRY_EDGE_PERSONALIZATION_NOT_BETTER"

    if pers_pos and (entry_nonpos or not entry_pos):
        return "SPECIFIC_PERSONALIZATION_ONLY"

    if yen_only_positive(sel):
        return "SPECIFIC_YEN_POSITIVE_BPS_NONPOSITIVE"

    if abs_pos:
        return "SPECIFIC_ABSOLUTE_POSITIVE_ONLY"

    return "SPECIFIC_MIXED"

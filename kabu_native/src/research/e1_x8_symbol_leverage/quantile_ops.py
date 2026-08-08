"""Quantile contract + membership helpers (reuse PFQ derive method)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x7_pfq.candidates import _quantile, derive_thresholds, passes_candidate
from research.e1_x7_pfq.config import FLOW_Q, MIN_CLASSIFIED_TRADES_30S, UPDATE_Q

from . import FROZEN


def reproduce_full_thresholds(audits: list[dict[str, Any]]) -> dict[str, Any]:
    thr = derive_thresholds(audits)
    ok = (
        abs(float(thr["price_update_count_10s_q70"]) - FROZEN["price_update_count_10s_q70"]) < 1e-12
        and abs(float(thr["uptick_volume_ratio_30s_q30"]) - FROZEN["uptick_volume_ratio_30s_q30"]) < 1e-12
    )
    return {
        "thresholds": thr,
        "matches_frozen": ok,
        "contract": {
            "quantile_impl": "candidates._quantile linear pos=q*(n-1)",
            "update_q": UPDATE_Q,
            "flow_q": FLOW_Q,
            "min_classified": MIN_CLASSIFIED_TRADES_30S,
            "update_missing_excluded": True,
            "flow_requires_ratio_valid": True,
            "update_compare": ">=",
            "flow_compare": "<=",
        },
    }


def thresholds_from_audits(audits: list[dict[str, Any]]) -> dict[str, float]:
    thr = derive_thresholds(audits)
    return {
        "price_update_count_10s_q70": float(thr["price_update_count_10s_q70"]),
        "uptick_volume_ratio_30s_q30": float(thr["uptick_volume_ratio_30s_q30"]),
        "pu_n": int(thr["pu_n"]),
        "flow_n": int(thr["flow_n"]),
    }


def membership_ids(audits: list[dict[str, Any]], thr: dict[str, Any], candidate_id: str) -> set[str]:
    return {a["episode_id"] for a in audits if passes_candidate(a, candidate_id, thr)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return (len(a & b) / len(u)) if u else 1.0


def tie_counts(values: list[float], thr: float) -> dict[str, Any]:
    below = sum(1 for v in values if v < thr - 1e-15)
    at = sum(1 for v in values if abs(v - thr) <= 1e-15)
    above = sum(1 for v in values if v > thr + 1e-15)
    n = len(values)
    return {
        "n": n,
        "n_below_threshold": below,
        "n_at_threshold": at,
        "n_above_threshold": above,
        "empirical_cdf_below": below / n if n else None,
        "empirical_cdf_at": (below + at) / n if n else None,
    }


def quantile_shift_sensitivity(values: list[float], q: float, thr: float) -> dict[str, Any]:
    """If discrete thr moves by 1 unit (update counts), how candidate n changes for >= thr."""
    # descriptive for integer-like update counts
    n_ge = sum(1 for v in values if v >= thr - 1e-15)
    n_ge_plus1 = sum(1 for v in values if v >= thr + 1.0 - 1e-15)
    n_ge_minus1 = sum(1 for v in values if v >= thr - 1.0 - 1e-15)
    return {
        "n_ge_thr": n_ge,
        "n_ge_thr_plus_1": n_ge_plus1,
        "n_ge_thr_minus_1": n_ge_minus1,
        "delta_if_thr_plus_1": n_ge_plus1 - n_ge,
        "delta_if_thr_minus_1": n_ge_minus1 - n_ge,
    }

"""PFQ candidate registry and build-only thresholds."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x7_pfq.config import CANDIDATES, FLOW_Q, MIN_CLASSIFIED_TRADES_30S, UPDATE_Q


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    if not ys:
        raise ValueError("empty quantile")
    if len(ys) == 1:
        return ys[0]
    pos = q * (len(ys) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ys) - 1)
    w = pos - lo
    return ys[lo] * (1 - w) + ys[hi] * w


def derive_thresholds(audits: list[dict[str, Any]]) -> dict[str, Any]:
    """Build-only feature quantiles — no PnL. Uses ratio_valid rows for flow; all with PU for update."""
    pu_vals = [float(a["price_update_count_10s"]) for a in audits if a.get("price_update_count_10s") is not None]
    flow_vals = [
        float(a["uptick_volume_ratio_30s"]) for a in audits
        if a.get("ratio_valid") and a.get("uptick_volume_ratio_30s") is not None
    ]
    thr = {
        "price_update_count_10s_q70": _quantile(pu_vals, UPDATE_Q),
        "uptick_volume_ratio_30s_q30": _quantile(flow_vals, FLOW_Q),
        "pu_n": len(pu_vals),
        "flow_n": len(flow_vals),
        "derivation": "build_only_feature_quantile",
        "no_pnl": True,
    }
    return thr


def candidate_registry(thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    pu = thresholds["price_update_count_10s_q70"]
    flow = thresholds["uptick_volume_ratio_30s_q30"]
    return [
        {
            "candidate_id": "PFQ_UPDATE_Q70",
            "rules": {
                "price_update_count_10s_ge": pu,
            },
            "purpose": "ablation: update activity only",
        },
        {
            "candidate_id": "PFQ_FLOW_Q30",
            "rules": {
                "uptick_volume_ratio_30s_le": flow,
                "classified_trade_count_30s_ge": MIN_CLASSIFIED_TRADES_30S,
                "ratio_valid": True,
            },
            "purpose": "ablation: avoid one-sided uptick flow",
        },
        {
            "candidate_id": "PFQ_JOINT",
            "rules": {
                "price_update_count_10s_ge": pu,
                "uptick_volume_ratio_30s_le": flow,
                "classified_trade_count_30s_ge": MIN_CLASSIFIED_TRADES_30S,
                "ratio_valid": True,
            },
            "purpose": "joint V4-supported feature hypothesis",
        },
    ]


def passes_candidate(audit: dict[str, Any], candidate_id: str, thresholds: dict[str, Any]) -> bool:
    pu_thr = thresholds["price_update_count_10s_q70"]
    flow_thr = thresholds["uptick_volume_ratio_30s_q30"]
    pu = audit.get("price_update_count_10s")
    ratio = audit.get("uptick_volume_ratio_30s")
    valid = bool(audit.get("ratio_valid"))
    clas = int(audit.get("classified_trade_count_30s") or 0)

    if candidate_id == "PFQ_UPDATE_Q70":
        return pu is not None and float(pu) >= pu_thr - 1e-12
    if candidate_id == "PFQ_FLOW_Q30":
        return (
            valid and ratio is not None and float(ratio) <= flow_thr + 1e-12
            and clas >= MIN_CLASSIFIED_TRADES_30S
        )
    if candidate_id == "PFQ_JOINT":
        return (
            pu is not None and float(pu) >= pu_thr - 1e-12
            and valid and ratio is not None and float(ratio) <= flow_thr + 1e-12
            and clas >= MIN_CLASSIFIED_TRADES_30S
        )
    return False


def assert_registry_max_three(registry: list) -> None:
    assert len(registry) <= 3
    assert len(CANDIDATES) == 3

"""Pre-profit feature distribution audit."""
from __future__ import annotations

import math
from typing import Any, Sequence

from research.integrated_order_flow_absorption_reversal.state_machine import Episode

DIST_KEYS = [
    "sell_trade_count", "sell_trade_qty", "sell_trade_ratio", "sell_frequency", "down_ticks",
    "sell_qty_per_down_tick", "down_tick_per_sell_qty", "bid_replenishment_count",
    "sell_impact_start", "sell_impact_end", "sell_impact_decay", "low_update_interval",
    "buy_trade_count", "buy_trade_qty", "buy_trade_ratio", "buy_frequency",
    "distance_entry_from_absorption", "spread_at_entry",
]


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = (len(ys) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return ys[int(k)]
    return ys[f] * (c - k) + ys[c] * (k - f)


def summarize_dist(values: list[Any]) -> dict[str, Any]:
    nums = [float(v) for v in values if v is not None and isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))]
    missing = 1.0 - (len(nums) / len(values)) if values else 1.0
    if not nums:
        return {"count": 0, "missing_rate": missing}
    mean = sum(nums) / len(nums)
    var = sum((x - mean) ** 2 for x in nums) / len(nums)
    return {
        "count": len(nums),
        "mean": mean,
        "std": math.sqrt(var),
        "min": min(nums),
        "p10": _pct(nums, 0.10),
        "p25": _pct(nums, 0.25),
        "p50": _pct(nums, 0.50),
        "p75": _pct(nums, 0.75),
        "p90": _pct(nums, 0.90),
        "max": max(nums),
        "missing_rate": missing,
    }


def build_feature_distributions(episodes: Sequence[Episode]) -> dict[str, Any]:
    bags: dict[str, list[Any]] = {k: [] for k in DIST_KEYS}
    bags["post_entry_MFE"] = []
    bags["post_entry_MAE"] = []
    bags["bid_survival_sec"] = []
    bags["seconds_since_last_low"] = []
    for ep in episodes:
        feat = ep.features or {}
        for k in DIST_KEYS:
            bags[k].append(feat.get(k))
        if ep.entry_idx is not None:
            bags["post_entry_MFE"].append(ep.mfe_pct)
            bags["post_entry_MAE"].append(ep.mae_pct)
    return {k: summarize_dist(v) for k, v in bags.items()}


def success_failure_compare(episodes: Sequence[Episode], trades_by_ep: dict[str, Any]) -> dict[str, Any]:
    """Compare ENTRY-prior features by outcome class (no future leak into conditions)."""
    groups: dict[str, list[Episode]] = {}
    for ep in episodes:
        if ep.entry_idx is None:
            continue
        tr = trades_by_ep.get(ep.episode_id)
        if not tr:
            continue
        groups.setdefault(tr.outcome, []).append(ep)

    def mean_feat(eps: list[Episode], key: str) -> Any:
        xs = [e.features.get(key) for e in eps if e.features.get(key) is not None]
        xs = [float(x) for x in xs]
        return (sum(xs) / len(xs)) if xs else None

    keys = ["sell_impact_decay", "bid_replenishment_count", "absorbed_sell_qty", "buy_trade_ratio", "distance_entry_from_absorption"]
    out = {}
    for g, eps in groups.items():
        out[g] = {"n": len(eps), **{k: mean_feat(eps, k) for k in keys}}
    return out

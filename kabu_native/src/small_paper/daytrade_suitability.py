"""
Phase 82: Daytrade suitability metrics (diagnostic only, no production wiring).
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping, Optional, Sequence

from small_paper.accepted_liquidity_metrics import (
    TIER_LARGE_JPY,
    TIER_MID_JPY,
    _float,
    _tier,
    _tier_ja,
    lookup_metrics_at_entry,
    metrics_from_payload,
)

QUALITY_GATE = 0.70
RULE_VOLATILITY_LIQUIDITY_TOP50 = "volatility_liquidity_top50"


def volatility_liquidity_score(
    atr_pct: Optional[float],
    trading_value_jpy: Optional[float],
) -> Optional[float]:
    """Phase83/84: atr_pct * log10(TradingValue)."""
    if atr_pct is None or trading_value_jpy is None or trading_value_jpy <= 0:
        return None
    return round(float(atr_pct) * math.log10(max(float(trading_value_jpy), 1.0)), 6)


def spread_pct_from_payload(payload: Mapping[str, Any]) -> Optional[float]:
    bid = _float(payload.get("BidPrice"))
    ask = _float(payload.get("AskPrice"))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100.0, 6)


def enrich_daytrade_metrics(
    base: Mapping[str, Optional[float]],
    payload: Mapping[str, Any],
) -> dict[str, Optional[float]]:
    out = dict(base)
    tv = base.get("trading_value_jpy")
    vol = base.get("trading_volume")
    mc = base.get("market_cap_jpy")
    atr = base.get("atr_pct")
    sp = spread_pct_from_payload(payload)

    turnover: Optional[float] = None
    if tv is not None and mc is not None and mc > 0:
        turnover = tv / mc

    vol_liq = volatility_liquidity_score(atr, tv)

    liq_parts: list[float] = []
    if tv is not None and tv > 0:
        liq_parts.append(math.log10(tv))
    if vol is not None and vol > 0:
        liq_parts.append(math.log10(vol))
    if sp is not None and sp > 0:
        liq_parts.append(1.0 / sp)
    liquidity_score = round(statistics.mean(liq_parts), 6) if liq_parts else None

    out["spread_pct"] = sp
    out["turnover_proxy"] = round(turnover, 8) if turnover is not None else None
    out["volatility_liquidity_score"] = vol_liq
    out["liquidity_score"] = liquidity_score
    return out


def metrics_at_entry_from_series(
    series: Sequence[tuple[float, dict[str, Optional[float]]]],
    entry_ts: float,
    payload_lookup: Optional[Mapping[str, Any]] = None,
    *,
    entry_price: float = 0.0,
) -> dict[str, Optional[float]]:
    base = lookup_metrics_at_entry(series, entry_ts)
    if payload_lookup:
        base = enrich_daytrade_metrics(
            {**base, **metrics_from_payload(payload_lookup, entry_price=entry_price)},
            payload_lookup,
        )
    else:
        base = enrich_daytrade_metrics(base, {})
    return base


def percentile_value(values: Sequence[float], pct: float) -> float:
    """pct in [0,1]; 0.75 = 75th percentile (top 25% cutoff)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * pct)))
    return ordered[idx]


def rank_normalize(values: Sequence[Optional[float]]) -> list[Optional[float]]:
    """Map values to [0,1] by rank (higher raw = higher norm)."""
    pairs = [(i, v) for i, v in enumerate(values) if v is not None]
    if not pairs:
        return [None] * len(values)
    n = len(pairs)
    sorted_pairs = sorted(pairs, key=lambda x: x[1])
    out: list[Optional[float]] = [None] * len(values)
    for rank, (idx, _) in enumerate(sorted_pairs):
        out[idx] = round(rank / max(1, n - 1), 6)
    return out


def attach_composite_scores(rows: list[dict[str, Any]]) -> None:
    """Add normalized components and daytrade_suitability_score in-place."""
    fields = ("atr_pct", "intraday_range_pct", "trading_value_jpy", "turnover_proxy")
    norms: dict[str, list[Optional[float]]] = {
        f: rank_normalize([r.get(f) for r in rows]) for f in fields
    }
    for i, row in enumerate(rows):
        na, nr, nt, nv = (
            norms["atr_pct"][i],
            norms["intraday_range_pct"][i],
            norms["trading_value_jpy"][i],
            norms["turnover_proxy"][i],
        )
        row["norm_atr_pct"] = na
        row["norm_intraday_range_pct"] = nr
        row["norm_trading_value"] = nt
        row["norm_turnover_proxy"] = nv
        if na is None and nr is None and nt is None and nv is None:
            row["daytrade_suitability_score"] = None
        else:
            row["daytrade_suitability_score"] = round(
                0.40 * (na or 0)
                + 0.30 * (nr or 0)
                + 0.20 * (nt or 0)
                + 0.10 * (nv or 0),
                6,
            )


def profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "accepted_count": 0,
            "structural_pf": None,
            "avg_pnl": None,
            "win_rate": None,
            "max_loss": None,
        }
    pnls = [float(t["realized_pnl_pct"]) for t in trades]
    pf = profit_factor(pnls)
    tier_counts = {}
    for t in trades:
        tier = str(t.get("market_cap_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    n = len(trades)
    return {
        "accepted_count": n,
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "max_loss": round(min(pnls), 4),
        "marketcap_tier_distribution": tier_counts,
        "large_share": round(tier_counts.get("large", 0) / n, 4),
        "mid_share": round(tier_counts.get("mid", 0) / n, 4),
        "small_share": round(tier_counts.get("small", 0) / n, 4),
    }


def policy_impact(
    baseline: Sequence[Mapping[str, Any]],
    kept: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_set = {(t["symbol"], t["entry_time"]) for t in baseline}
    kept_set = {(t["symbol"], t["entry_time"]) for t in kept}
    dropped = base_set - kept_set
    missed_winners = 0
    avoided_losers = 0
    for t in baseline:
        key = (t["symbol"], t["entry_time"])
        if key not in dropped:
            continue
        pnl = float(t.get("realized_pnl_pct") or 0.0)
        if pnl > 0:
            missed_winners += 1
        elif pnl < 0:
            avoided_losers += 1
    return {
        "rejected_by_suitability": len(dropped),
        "missed_winners": missed_winners,
        "avoided_losers": avoided_losers,
    }


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return None
    return round(num / (dx * dy), 4)


def tier_label(mc: Optional[float]) -> str:
    return _tier(mc)


def tier_ja(tier: str) -> str:
    return _tier_ja(tier)

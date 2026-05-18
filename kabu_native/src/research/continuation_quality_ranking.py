"""
Phase 38: Continuation quality ranking (validation only — no new EXIT logic).
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.research_exit_criteria import _as_float

QUALITY_WEIGHTS = {
    "momentum_continuation": 0.30,
    "bullish_duration": 0.22,
    "favorable_continuation": 0.20,
    "bearish_accumulation_inverse": 0.14,
    "continuation_stability": 0.14,
}

MIN_QUALITY_SMALL_PAPER = 0.42
DURATION_SCALE = 14.0


def _norm_duration(ticks: float) -> float:
    if ticks <= 0:
        return 0.0
    return min(1.0, ticks / DURATION_SCALE)


def continuation_components(trade: Mapping[str, Any]) -> dict[str, float]:
    """Component scores used by continuation_quality_score (observer display only)."""
    mom = _as_float(trade.get("momentum_continuation_score"))
    bull = _as_float(trade.get("bullish_continuation_score")) or _as_float(
        trade.get("bullish_weighted_score")
    )
    fav = _as_float(trade.get("favorable_continuation")) or _as_float(
        trade.get("favorable_weighted")
    )
    bear = _as_float(trade.get("bearish_accumulation_score")) or _as_float(
        trade.get("bearish_weighted_score")
    )
    dur = _as_float(trade.get("max_momentum_continuation_duration")) or _as_float(
        trade.get("max_continuation_duration")
    )
    mfe = _as_float(trade.get("max_favorable_excursion_pct")) or 0.0
    mae = abs(_as_float(trade.get("max_adverse_excursion_pct")) or 0.0)

    if mom is None:
        mom = min(1.0, max(0.0, (mfe - 0.4 * mae) / 0.35)) if mfe or mae else 0.25
    if bull is None:
        bull = min(1.0, max(0.0, mfe / 0.25)) if mfe else 0.2
    if fav is None:
        fav = min(1.0, max(0.0, mfe / 0.3)) if mfe else 0.15

    mom_f = float(mom or 0)
    bull_f = float(bull or 0)
    fav_f = float(fav or 0)
    bear_f = float(bear or 0)
    dur_n = _norm_duration(float(dur or 0))
    bear_inv = max(0.0, 1.0 - min(1.0, bear_f))
    stability = 1.0 if mfe > mae else max(0.0, 0.5 + (mfe - mae) / 0.5)
    quality = min(
        1.0,
        QUALITY_WEIGHTS["momentum_continuation"] * mom_f
        + QUALITY_WEIGHTS["bullish_duration"] * dur_n
        + QUALITY_WEIGHTS["favorable_continuation"] * fav_f
        + QUALITY_WEIGHTS["bearish_accumulation_inverse"] * bear_inv
        + QUALITY_WEIGHTS["continuation_stability"] * stability
        + 0.04 * bull_f,
    )
    return {
        "continuation_quality": round(quality, 4),
        "momentum_continuation": round(mom_f, 4),
        "bullish_continuation": round(bull_f, 4),
        "favorable_continuation": round(fav_f, 4),
        "bearish_accumulation": round(bear_f, 4),
        "continuation_persistence": round(dur_n, 4),
        "max_favorable_excursion_pct": round(float(mfe), 4),
        "max_adverse_excursion_pct": round(float(mae), 4),
    }


def continuation_quality_score(trade: Mapping[str, Any]) -> float:
    """Rank trades by continuation strength; uses enriched fields or MFE/MAE proxy."""
    return continuation_components(trade)["continuation_quality"]


def rank_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    profile: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        if profile and str(t.get("profile")) != profile:
            continue
        q = continuation_quality_score(t)
        rows.append(
            {
                "symbol": t.get("symbol"),
                "trade_date": t.get("trade_date"),
                "profile": t.get("profile"),
                "pnl_pct": _as_float(t.get("pnl_pct")),
                "continuation_quality_score": round(q, 4),
                "momentum_continuation_score": t.get("momentum_continuation_score"),
                "exit_reason": t.get("exit_reason"),
            }
        )
    rows.sort(key=lambda r: r["continuation_quality_score"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["quality_rank"] = i
    return rows


def build_continuation_quality_distribution(
    trades: Sequence[Mapping[str, Any]],
    *,
    focus_profile: str,
) -> dict[str, Any]:
    ranked = rank_trades(trades, profile=focus_profile)
    scores = [r["continuation_quality_score"] for r in ranked]
    winners = [r for r in ranked if (r.get("pnl_pct") or 0) > 0]
    losers = [r for r in ranked if (r.get("pnl_pct") or 0) < 0]

    def _mean(grp: Sequence[Mapping[str, Any]]) -> Optional[float]:
        if not grp:
            return None
        return statistics.mean([r["continuation_quality_score"] for r in grp])

    tiers = {
        "top_quartile": [r for r in ranked if r["continuation_quality_score"] >= 0.55],
        "above_median": [r for r in ranked if r["continuation_quality_score"] >= 0.42],
        "below_median": [r for r in ranked if r["continuation_quality_score"] < 0.42],
    }
    tier_pf: dict[str, Any] = {}
    for name, grp in tiers.items():
        pnls = [_as_float(r.get("pnl_pct")) or 0.0 for r in grp]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gl = abs(sum(losses))
        tier_pf[name] = {
            "count": len(grp),
            "avg_pnl_pct": statistics.mean(pnls) if pnls else None,
            "profit_factor": (sum(wins) / gl) if gl > 0 else None,
            "win_rate": len(wins) / len(pnls) if pnls else None,
        }

    return {
        "phase": 38,
        "focus_profile": focus_profile,
        "trade_count": len(ranked),
        "score_distribution": {
            "p25": statistics.quantiles(scores, n=4)[0] if len(scores) >= 4 else None,
            "p50": statistics.median(scores) if scores else None,
            "p75": statistics.quantiles(scores, n=4)[2] if len(scores) >= 4 else None,
            "mean": statistics.mean(scores) if scores else None,
        },
        "winner_loser_gap": (
            (_mean(winners) - _mean(losers)) if winners and losers else None
        ),
        "tier_performance": tier_pf,
        "min_quality_small_paper": MIN_QUALITY_SMALL_PAPER,
        "top_ranked_sample": ranked[:20],
    }

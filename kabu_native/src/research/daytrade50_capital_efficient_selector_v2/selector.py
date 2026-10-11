"""DAYTRADE50_SELECTOR_V2. Equal-rank median. No fitted weights. No Core10."""
from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.daytrade50_capital_efficient_selector_v2.features import percentile_high_is_good
from research.daytrade50_capital_efficient_selector_v2.policy import (
    AWAIT_USER_G,
    BUDGET_INELIGIBLE_PRESELECT,
    SCORE_PRECISION,
    SELECTOR_ID,
    budget_share,
    one_lot_notional,
    preselect_status,
)

RANK_FIELDS = (
    "liquidity_feature",
    "range_pct_5d_median",
    "range_pct_20d_median",
    "suitability_feature",
)
RANK_NAMES = (
    "LIQUIDITY_RANK",
    "OPPORTUNITY_5D_RANK",
    "OPPORTUNITY_20D_RANK",
    "EXECUTION_SUITABILITY_RANK",
)
FAIL_CLOSED = "FAIL_CLOSED_UNDER_50"


def quality_score(ranks: Sequence[float]) -> float:
    if len(ranks) != 4:
        raise ValueError("quality score is the median of four ranks")
    return float(statistics.median(ranks))


def score_cross_section(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Percentile ranks inside the already eligible cross-section. Budget share is not a rank."""
    scored: list[dict[str, Any]] = [dict(row) for row in rows]
    for field, name in zip(RANK_FIELDS, RANK_NAMES):
        pairs = [(str(row["symbol"]), float(row[field])) for row in scored]
        ranks = percentile_high_is_good(pairs)
        for row in scored:
            row[name] = ranks[str(row["symbol"])]
    for row in scored:
        ranks = [float(row[name]) for name in RANK_NAMES]
        row["PRIMARY_QUALITY_SCORE"] = quality_score(ranks)
        row["score_rounded"] = round(float(row["PRIMARY_QUALITY_SCORE"]), SCORE_PRECISION)
    return scored


def selection_sort_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    return (
        -float(row["score_rounded"]),
        float(row["BUDGET_SHARE"]),
        str(row["symbol"]),
    )


def select_top50_from_freeze(
    feature_rows: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any],
) -> dict[str, Any]:
    """Score with the G copied at run start. A later store write is not read."""
    frozen = freeze.get("SELECTOR_EFFECTIVE_G")
    gross = None if frozen is None else float(frozen)
    result = select_top50(feature_rows, gross_budget_yen=gross)
    result["SELECTOR_EFFECTIVE_G"] = None if frozen is None else int(frozen)
    result["POLICY_INSTANCE_SHA"] = freeze.get("POLICY_INSTANCE_SHA")
    return result


def select_top50(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    gross_budget_yen: Optional[float],
) -> dict[str, Any]:
    """Hard eligibility, then score, then 50. Missing targets cannot refill this list."""
    if gross_budget_yen is None:
        return {
            "selector_id": SELECTOR_ID,
            "status": AWAIT_USER_G,
            "symbols": [],
            "eligible_n": 0,
            "gross_budget_yen": None,
            "core10_used": False,
        }
    if float(gross_budget_yen) <= 0:
        raise ValueError("G must be positive")
    eligible: list[dict[str, Any]] = []
    rejected = {"history": 0, "budget": 0, "other": 0}
    for raw in feature_rows:
        row = dict(raw)
        if not row.get("eligible"):
            rejected["history" if row.get("reject") == "INSUFFICIENT_HISTORY" else "other"] += 1
            continue
        status = preselect_status(float(row["prior_close"]), float(gross_budget_yen))
        row["preselect_status"] = status
        row["ONE_LOT_NOTIONAL"] = one_lot_notional(float(row["prior_close"]))
        row["BUDGET_SHARE"] = budget_share(float(row["prior_close"]), float(gross_budget_yen))
        if status == BUDGET_INELIGIBLE_PRESELECT:
            rejected["budget"] += 1
            continue
        eligible.append(row)
    scored = score_cross_section(eligible)
    ordered = sorted(scored, key=selection_sort_key)
    if len(ordered) < 50:
        return {
            "selector_id": SELECTOR_ID,
            "status": FAIL_CLOSED,
            "symbols": [],
            "eligible_n": len(ordered),
            "rejected": rejected,
            "gross_budget_yen": float(gross_budget_yen),
            "core10_used": False,
            "rows": ordered,
        }
    chosen = ordered[:50]
    for index, row in enumerate(chosen, start=1):
        row["rank"] = index
        row["origin"] = "DAYTRADE50_RULE"
    return {
        "selector_id": SELECTOR_ID,
        "status": "TOP50",
        "symbols": [str(row["symbol"]) for row in chosen],
        "eligible_n": len(ordered),
        "rejected": rejected,
        "gross_budget_yen": float(gross_budget_yen),
        "core10_used": False,
        "rows": chosen,
    }

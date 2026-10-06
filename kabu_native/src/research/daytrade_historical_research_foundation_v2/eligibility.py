"""Trade eligibility labels. No PnL. No future return. No target N."""
from __future__ import annotations

from typing import Any

from research.daytrade_historical_research_foundation_v2 import (
    ACTIVE_MINUTE_RATIO_FLOOR,
    NOTIONAL_REVIEW_JPY,
    RANGE_BPS_TRADE_FLOOR,
)

LABEL_TRADE = "TRADE_ELIGIBLE_CANDIDATE"
LABEL_CONTEXT = "CONTEXT_CANDIDATE"
LABEL_LOW = "LOW_DAYTRADE_UTILITY"
LABEL_OPS = "OPERABILITY_REVIEW_REQUIRED"


def _base(pool_row: dict[str, Any], minute_row: dict[str, Any] | None) -> dict[str, Any]:
    out = {
        "symbol": pool_row["symbol"],
        "name_en": pool_row.get("name_en"),
        "tse33_name": pool_row.get("tse33_name"),
        "in_v1_seed": bool(pool_row.get("in_v1_seed")),
        "in_liquidity_top80": bool(pool_row.get("in_liquidity_top80")),
        "in_activity_top40": bool(pool_row.get("in_activity_top40")),
        "special_excluded_9983_6861": False,
    }
    if minute_row:
        for k, v in minute_row.items():
            if k not in out:
                out[k] = v
    return out


def classify(*, pool_row: dict[str, Any], minute_row: dict[str, Any] | None) -> dict[str, Any]:
    reasons: list[str] = []
    range_bps = pool_row.get("median_range_bps")
    notional = pool_row.get("notional_100")
    if minute_row and minute_row.get("notional_100") is not None:
        notional = minute_row.get("notional_100")
    base = _base(pool_row, minute_row)
    if notional is not None and float(notional) >= float(NOTIONAL_REVIEW_JPY):
        return {
            **base,
            "label": LABEL_OPS,
            "entry_eligible": False,
            "reasons": ["100_share_notional_review"],
            "notional_100": notional,
            "median_range_bps": range_bps,
        }
    if not minute_row or not minute_row.get("minute_metrics_complete"):
        return {
            **base,
            "label": LABEL_OPS,
            "entry_eligible": False,
            "reasons": ["minute_metrics_incomplete"],
            "notional_100": notional,
            "median_range_bps": range_bps,
            "minute_metrics_complete": False,
        }
    if range_bps is None or float(range_bps) < float(RANGE_BPS_TRADE_FLOOR):
        reasons.append("median_daily_range_bps_below_floor")
    amr = minute_row.get("active_minute_ratio")
    if amr is None or float(amr) < float(ACTIVE_MINUTE_RATIO_FLOOR):
        reasons.append("active_minute_ratio_below_floor")
    first = minute_row.get("first_half_median_va")
    last = minute_row.get("second_half_median_va")
    if first and last and first > 0 and last > 0:
        if last < 0.25 * first or first < 0.25 * last:
            reasons.append("first_second_half_unstable")
    if reasons:
        label = LABEL_LOW
        if pool_row.get("in_liquidity_top80") and "median_daily_range_bps_below_floor" in reasons and len(reasons) == 1:
            label = LABEL_CONTEXT
        return {
            **base,
            "label": label,
            "entry_eligible": False,
            "reasons": reasons,
            "notional_100": notional,
            "median_range_bps": range_bps,
            "active_minute_ratio": amr,
        }
    return {
        **base,
        "label": LABEL_TRADE,
        "entry_eligible": True,
        "reasons": ["liquidity_pass", "range_pass", "stability_pass"],
        "notional_100": notional,
        "median_range_bps": range_bps,
        "active_minute_ratio": amr,
    }


assert LABEL_TRADE.startswith("TRADE")
assert RANGE_BPS_TRADE_FLOOR == 60.0

"""Corporate-action plan. Adjusted returns vs as-traded execution. Do not mix."""
from __future__ import annotations

from typing import Any


def plan() -> dict[str, Any]:
    return {
        "minute_and_tick_are_unadjusted": True,
        "daily_has_AdjFactor": True,
        "adjusted_series_use": "price_return_research",
        "unadjusted_series_use": "actual_price_execution_proxy",
        "mix_forbidden": True,
        "method": (
            "From J-Quants daily bars, take AdjFactor on ex-date. "
            "CumAdj = product of later AdjFactors. "
            "AdjPrice = unadjusted * CumAdj. "
            "AdjVolume = unadjusted / CumAdj. "
            "Apply the daily CumAdj to every minute bar on that date."
        ),
        "supported_actions": ("split", "reverse_split", "rights_issue_if_in_AdjFactor"),
        "unsupported_or_partial": (
            "some_non_split_corporate_actions_not_in_JQuants_AdjFactor"
        ),
        "ex_date_rule": "do_not_trade_across_unadjusted_gap_without_flag",
        "listing_continuity": "drop_or_flag_names_with_code_change_merger_until_map_exists",
        "etf_rights": "ETF_not_in_stock_universe; if proxy used, still apply factor if provided",
        "plan_ready": True,
        "applied_to_panel": False,
    }


def rows() -> list[dict[str, Any]]:
    p = plan()
    return [
        {"key": k, "value": v}
        for k, v in p.items()
    ]


assert plan()["mix_forbidden"] is True
assert plan()["plan_ready"] is True
assert plan()["applied_to_panel"] is False

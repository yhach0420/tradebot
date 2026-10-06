"""Causally available information already in the research stack. No new labels. No future files."""
from __future__ import annotations

from typing import Any


def available_information() -> dict[str, Any]:
    families = [
        {
            "FAMILY": "COMPLETED_1M_OHLCV",
            "SOURCE": "simple_tech_entry_family.bars / V3-V4 SessionEngine pending bars",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "COMPLETED_HTF_BARS",
            "SOURCE": "simple_tech_entry_family.v7_bars.aggregate_bars (3m/5m, partial buckets dropped)",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "MA_EMA",
            "SOURCE": "simple_tech_entry_family.indicators.ema (9/21)",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "BOLLINGER",
            "SOURCE": "simple_tech_entry_family.indicators.bollinger (20,2)",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "RCI",
            "SOURCE": "simple_tech_entry_family.indicators.rci_series (9, -80 cross)",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "VOLUME",
            "SOURCE": "S_VOL_CONFIRM_1M volume_confirm; also raw 1m volume and participation percentiles",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "ACTIVITY_FEATURES",
            "SOURCE": "participation_onset T1; TradingVolume deltas",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "FROZEN_DAY_UNIVERSE",
            "SOURCE": "_p1_inventory.resolve_universe (AM registration / AM CSV, not capture-discovered)",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "CROSS_SECTIONAL_MARKET_STATE",
            "SOURCE": "CSB N_up counts; C4 first-arrival uniqueness; OR rank",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "CAUSAL_QUOTE_FRESHNESS",
            "SOURCE": "AskTime / BidTime then ingress; CurrentPriceTime forbidden for freshness",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "BOARD_SUPPORT",
            "SOURCE": "board_row / board_support as execution or filter; not a new learned label",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "SESSION_VWAP",
            "SOURCE": "session cumulative VWAP on completed 1m",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "PREVIOUS_HIGHS",
            "SOURCE": "Breakout P1 max of previous 5 completed Highs",
            "NEW_LABEL": False,
        },
        {
            "FAMILY": "PORTFOLIO_OCCUPANCY",
            "SOURCE": "CAP=5, same-symbol, slot at EXIT fill, 11:29 flatten (V3/V4 engine)",
            "NEW_LABEL": False,
        },
    ]
    return {
        "FAMILIES": families,
        "FAMILY_N": len(families),
        "FAMILY_IDS": [str(x["FAMILY"]) for x in families],
        "NEW_LEARNED_LABELS": False,
        "FUTURE_DATA_OPENED": False,
        "HOLDOUT_READ": False,
        "STRESS_READ": False,
    }

"""Market convention for MA periods by timeframe. Sources cited. No period search."""
from __future__ import annotations

from typing import Any


def convention_audit() -> dict[str, Any]:
    daily = {
        "timeframe": "DAILY",
        "common_ma": ["SMA5", "SMA25", "SMA75", "SMA100", "SMA200"],
        "primary_triple": ["SMA5", "SMA25", "SMA75"],
        "calendar_meaning": {
            "SMA5": "about 1 trading week",
            "SMA25": "about 1 trading month (legacy TSE 25-session month)",
            "SMA75": "about 1 quarter / 3 months",
        },
        "roles": {
            "SMA5": "short-term daily momentum / stock selection screen",
            "SMA25": "standard daily trend and dynamic support/resistance",
            "SMA75": "higher-timeframe daily bias, not an intraday entry trigger",
        },
        "commonly_displayed": True,
        "used_for": ["trend", "dynamic support/resistance", "stock selection", "pullback location", "higher-timeframe bias"],
        "also_visible": ["prior day high", "prior day low", "prior close", "gap vs prior close", "recent daily range"],
        "sources": [
            {
                "kind": "official_broker_docs",
                "org": "Mitsubishi UFJ e-Smart Securities (kabu.com)",
                "url": "https://kabu.com/beginner/stock/chart.html",
                "quote": "日足：5日、25日、75日、100日、200日",
            },
            {
                "kind": "major_broker_education",
                "org": "Daiwa Securities",
                "url": "https://www.daiwa.jp/seminar/technical/06/",
                "quote": "日足チャート MA(5)/MA(25)/MA(75); 5日 and 25日 come from 5 trading days/week and ~20-25 sessions/month",
            },
            {
                "kind": "retail_education",
                "org": "Zai Online / Diamond",
                "url": "https://diamond.jp/zai/articles/-/130284",
                "quote": "日足には、5日、25日、75日の移動平均線がよく使われる",
            },
        ],
    }
    five_min = {
        "timeframe": "5-MINUTE",
        "do_not_assume_same_meaning_as_daily": True,
        "if_same_numbers_copied": {
            "SMA5": "25 minutes of trading",
            "SMA25": "about 125 minutes (~one half-session)",
            "SMA75": "about 375 minutes (~more than one full cash session)",
        },
        "platform": {
            "kabu_station_supports_5m": True,
            "source": "https://kabu.com/kabustation/manual/chart12.html",
            "default_ma_periods_documented": "NOT_IN_OFFICIAL_CHART_MANUAL",
            "ma_overlay_max_n": 5,
            "vwap_available": True,
        },
        "common_intraday_references": ["VWAP", "opening range", "previous-day high/low/close", "volume", "price action", "optional short MA 5/20/25"],
        "sma75_on_5m_as_quarter_trend": False,
        "sma75_on_5m_supported_as_daily_convention": False,
        "note": "Copying 5/25/75 onto 5-minute bars does not preserve week/month/quarter meaning.",
    }
    fifteen_min = {
        "timeframe": "15-MINUTE",
        "if_same_numbers_copied": {
            "SMA5": "75 minutes",
            "SMA25": "about 6.25 hours (more than one session)",
            "SMA75": "about 3+ sessions",
        },
        "typical_role": "intraday structure / location between daily bias and 1-5m timing",
        "kabu_station_supports_15m": True,
    }
    one_min = {
        "timeframe": "1-MINUTE",
        "primary_role": "entry / micro price-action timing",
        "primary_market_structure_assumption": False,
        "if_same_numbers_copied": {
            "SMA5": "5 minutes",
            "SMA25": "25 minutes",
            "SMA75": "75 minutes",
        },
        "users_exist": True,
        "belongs_in_our_common_market_playbook_as_structure": False,
        "this_machine_saved_layout": "1-minute mini-charts with MA overlay off",
    }
    return {
        "daily_5_25_75_is_the_named_convention": True,
        "intraday_5_25_75_is_not_the_same_object": True,
        "confused_daily_convention_with_intraday_bar_periods": True,
        "daily": daily,
        "five_minute": five_min,
        "fifteen_minute": fifteen_min,
        "one_minute": one_min,
        "prior_day_levels": {
            "prior_high": {"role": "LOCATION", "available": True},
            "prior_low": {"role": "LOCATION", "available": True},
            "prior_close": {"role": "LOCATION / gap baseline", "available": True},
            "gap": {"role": "SELECTION / BIAS", "available": True},
            "recent_daily_range": {"role": "SELECTION / BIAS", "available": True},
        },
    }

"""Causal information available for 'why this stock today'. Missing items marked UNAVAILABLE."""
from __future__ import annotations

from typing import Any


def availability_audit() -> dict[str, Any]:
    return {
        "gap": {
            "status": "AVAILABLE",
            "source": "session open vs prior completed daily close on native 1m foundation",
            "causal": True,
        },
        "opening_activity": {
            "status": "AVAILABLE",
            "source": "native 1m OHLCV and TradingValue from 09:00",
            "causal": True,
        },
        "relative_volume_trading_value": {
            "status": "AVAILABLE",
            "source": "ClockHistory TV_CLOCK_PCTL, same-minute prior 20 days min 10 observations",
            "causal": True,
        },
        "sector_leadership": {
            "status": "AVAILABLE",
            "source": "105-name JPX tse33 panel used in peer-propagation research",
            "causal": True,
            "note": "sector relative, not a full TSE tape",
        },
        "market_futures_context": {
            "status": "UNAVAILABLE",
            "source": "TRUE_CME path blocked; Discovery 1m foundation is cash-equity native minutes, not a historical futures tape",
            "live_capture_exists_after_discovery": True,
            "do_not_silently_replace": True,
        },
        "peer_leadership": {
            "status": "AVAILABLE",
            "source": "same-tse33 peer panel, active_peer_n>=3",
            "causal": True,
            "economic_note": "peer lead was real then mostly consumed by next-open",
        },
        "recent_daily_volatility": {
            "status": "AVAILABLE",
            "source": "prior-session ATR20 / daily range from completed days",
            "causal": True,
        },
        "recent_catalyst_news": {
            "status": "UNAVAILABLE",
            "source": "kabu STATION NewsData.xml NewsItems empty; no historical news store in the native Discovery foundation",
            "do_not_silently_replace": True,
        },
        "opening_range": {
            "status": "AVAILABLE",
            "source": "construct OR5/OR15 from completed 1m bars after 09:05 / 09:15",
            "causal": True,
        },
        "vwap": {
            "status": "AVAILABLE",
            "source": "session VWAP from native 1m typical-price*volume; kabu STATION documents VWAP as a chart overlay",
            "causal": True,
        },
        "prior_day_high_low_close": {
            "status": "AVAILABLE",
            "source": "completed prior daily bar",
            "causal": True,
        },
        "historical_bid_ask_queue": {
            "status": "UNAVAILABLE",
            "do_not_silently_replace": True,
        },
        "missing_stock_selection_layer": True,
        "current_research_started_after_the_name_was_already_in_the_105_pool": True,
    }

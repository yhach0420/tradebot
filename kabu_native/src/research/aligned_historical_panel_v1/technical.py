"""Technical foundation library only. No signal generation. No ranking. No future return."""
from __future__ import annotations

from typing import Any

from research.fixed_universe_historical_foundation_v1 import (
    BB_PERIOD,
    BB_SIGMA,
    EMA_PERIODS,
    RCI_PERIODS,
    RSI_PERIODS,
    SAME_CLOCK_BASELINE_N_DAYS,
    SMA_PERIODS,
)
from research.fixed_universe_historical_foundation_v1.technical import (
    bollinger,
    ema,
    rci_series,
    relative_volume,
    rolling_high,
    rolling_low,
    rsi_wilder,
    same_clock_baseline,
    session_vwap,
    sma,
)


def technical_foundation() -> dict[str, Any]:
    return {
        "library_ready": True,
        "signals_generated": False,
        "strategy_ranking": False,
        "future_return_comparison": False,
        "parameter_search": False,
        "functions": [
            "sma",
            "ema",
            "session_vwap",
            "rci_series",
            "rsi_wilder",
            "bollinger",
            "rolling_high",
            "rolling_low",
            "relative_volume",
            "same_clock_baseline",
        ],
        "sma_periods": list(SMA_PERIODS),
        "ema_periods": list(EMA_PERIODS),
        "rci_periods": list(RCI_PERIODS),
        "rsi_periods": list(RSI_PERIODS),
        "bb_period": int(BB_PERIOD),
        "bb_sigma": float(BB_SIGMA),
        "same_clock_baseline_n_days": list(SAME_CLOCK_BASELINE_N_DAYS),
        "same_clock_rule": "compare_09:20_volume_to_prior_N_sessions_09:20_using_only_T_or_earlier",
        "available_at": "feature_from_bar_T_usable_at_bar_end",
        "imported_ok": all(
            callable(fn)
            for fn in (
                sma,
                ema,
                session_vwap,
                rci_series,
                rsi_wilder,
                bollinger,
                rolling_high,
                rolling_low,
                relative_volume,
                same_clock_baseline,
            )
        ),
    }


assert technical_foundation()["signals_generated"] is False
assert technical_foundation()["library_ready"] is True

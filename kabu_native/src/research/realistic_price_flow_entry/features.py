"""Causal feature views for RPFE (no time/symbol/date features)."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from research.pbv2_zero_base_revalidation.panel import CandidateRow

# Predefined stage feature pools (max 1–2 used per stage at fit time)
PATTERN_A_POOL = {
    "CONTEXT_READY": ("f_vwap", "f_rise5", "f_rise10", "f_near_high", "f_tv", "f_mom", "f_spread", "f_atr"),
    "SETUP_DETECTED": ("f_fall", "f_bounce", "f_rise5", "f_atr", "f_spread"),
    "SELL_PRESSURE_WEAKENED": ("f_np_ret_60", "f_np_ret_30", "f_fall", "f_spread", "f_mom"),
    "BUY_PRESSURE_CONFIRMED": (
        "f_mom",
        "f_np_tv_chg_pct_60",
        "f_np_ticks_60",
        "f_np_imb_chg_60",
        "f_np_bid_chg_60",
        "f_np_ask_chg_60",
        "f_bounce",
    ),
    "PRICE_TRIGGERED": ("f_rise5", "f_mom", "f_np_tv_chg_pct_60", "f_spread", "f_bounce"),
}

PATTERN_B_POOL = {
    "CONTEXT_READY": ("f_vwap", "f_near_high", "f_rise5", "f_tv", "f_spread", "f_mom"),
    "SETUP_DETECTED": ("f_atr", "f_spread", "f_rise5", "f_near_high", "f_mom"),
    "SELL_PRESSURE_WEAKENED": ("f_np_ret_60", "f_spread", "f_mom", "f_fall"),  # no new low / sell fade
    "BUY_PRESSURE_CONFIRMED": (
        "f_np_imb_chg_60",
        "f_np_bid_chg_60",
        "f_np_ask_chg_60",
        "f_np_tv_chg_pct_60",
        "f_np_ticks_60",
        "f_mom",
    ),
    "PRICE_TRIGGERED": ("f_near_high", "f_mom", "f_np_tv_chg_pct_60", "f_spread", "f_rise5"),
}

FLOW_KEYS = ("f_np_imb_chg_60", "f_np_bid_chg_60", "f_np_ask_chg_60", "f_np_tv_chg_pct_60")


def fget(row: CandidateRow, key: str) -> Optional[float]:
    v = row.features.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dynamic_complete(row: CandidateRow) -> bool:
    return bool(row.lane_c_complete and all(fget(row, k) is not None for k in FLOW_KEYS))


def stale_or_insufficient(row: CandidateRow) -> Optional[str]:
    if row.price_age_sec is not None and row.price_age_sec > 5.0:
        return "price_stale"
    if row.board_age_sec is not None and row.board_age_sec > 5.0:
        return "board_stale"
    # need minimal dense history proxies
    if fget(row, "f_rise5") is None and fget(row, "f_mom") is None:
        return "price_history_insufficient"
    return None


def feature_lineage() -> list[dict[str, Any]]:
    rows = []
    for pat, pool in (("A", PATTERN_A_POOL), ("B", PATTERN_B_POOL)):
        for state, feats in pool.items():
            for f in feats:
                rows.append(
                    {
                        "pattern": pat,
                        "state": state,
                        "feature": f,
                        "source": "candidate_panel_event_or_np",
                        "imputation": "none",
                        "time_feature": False,
                        "symbol_feature": False,
                    }
                )
    return rows

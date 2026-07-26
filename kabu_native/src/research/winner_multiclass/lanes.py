"""Feature lanes for Winner Multiclass (time features fully excluded)."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

TIME_BLOCKLIST = (
    "mkt_minutes_from_open",
    "mkt_minutes_to_refresh",
    "mkt_near_refresh",
    "mkt_session_am",
    "f_day_high_from_open",
    "f_minutes_since_day_high",
    "minutes_from_open",
    "minutes_to_refresh",
    "session_am",
    "day_high",
    "weekday",
    "dow",
    "symbol",
    "clock",
    "am_pm",
)

LANE_A = {
    "f_tv", "vol_tv", "f_vwap", "px_vwap_dev", "f_atr", "px_atr",
    "f_near_high", "px_near_high", "f_mom", "f_mom_alt", "tech_mom",
    "f_entry_mom_score", "f_chase", "tech_chase", "f_rise5", "f_rise10", "f_rise15",
    "px_ma_proxy_5m", "px_ma_proxy_10m", "f_r30", "f_r60", "f_r120",
    "w_30s_ret", "w_60s_ret", "w_120s_ret", "w_5m_ret",
    "f_bounce", "f_fall", "f_slope5", "f_pbv2", "tech_pbv2",
    "px_bounce_from_low", "px_fall_from_high", "mom_pullback_rate", "mom_rebound_rate",
}

LANE_B = {"board_imb", "f_imb", "board_imb_pct", "f_imb_pct"}

LANE_C = {
    "board_spread", "f_spread", "board_age", "f_board_age",
    "f_np_imb_chg_60", "w_60s_imb_chg", "f_np_imb_chg_30", "f_np_imb_chg_120", "f_np_imb_chg_300",
    "f_np_bid_chg_60", "f_np_ask_chg_60", "w_60s_bid_chg", "w_60s_ask_chg",
    "vol_surge_60s", "w_60s_tv_chg", "f_np_tv_chg_pct_60", "f_np_tv_chg_pct_30", "f_np_tv_chg_pct_120",
    "f_np_imb_persist_60", "w_60s_imb_persist",
    "board_div_price_up_board_down", "f_div_price_up_board_down",
    "w_60s_ticks", "w_60s_exec_speed", "w_30s_exec_speed", "w_120s_exec_speed",
}

SUSPECT_B_DAYS = ("20260615", "20260616", "20260617", "20260618", "20260619")


def is_time_or_id_feature(name: str) -> bool:
    low = name.lower()
    if any(b in low for b in TIME_BLOCKLIST):
        return True
    if low in ("symbol", "day", "session", "position_id"):
        return True
    return False


def lane_of(name: str) -> str:
    if name in LANE_A:
        return "A"
    if name in LANE_B:
        return "B"
    if name in LANE_C:
        return "C"
    if is_time_or_id_feature(name):
        return "EXCLUDED"
    n = name.lower()
    if any(x in n for x in ("spread", "board_age", "np_imb", "np_bid", "np_ask", "tv_chg", "vol_surge", "exec_speed")):
        return "C"
    if "imb" in n:
        return "B"
    return "A"


def select_lane_features(all_names: Sequence[str], *, lanes: Sequence[str]) -> list[str]:
    allow = set(lanes)
    out = []
    for n in all_names:
        if is_time_or_id_feature(n):
            continue
        if lane_of(n) in allow:
            out.append(n)
    return sorted(out)
